import itertools
import logging
from abc import ABCMeta, abstractmethod
from asyncio import IncompleteReadError, StreamReader, StreamWriter
from typing import AsyncIterator, Iterable

from aiofiles.threadpool.binary import AsyncBufferedIOBase

from kalandra.auth.basic import CredentialProvider
from kalandra.gitprotocol import NULL_OBJECT_ID, PacketLine, PacketLineType, Ref, RefChange

logger = logging.getLogger(__name__)

MEGABYTES = 1024 * 1024


class ConnectionException(Exception):
    pass


class Transport(metaclass=ABCMeta):
    def __init__(self, *, url: str, credentials_provider: CredentialProvider):
        self._url = url
        self.credentials_provider = credentials_provider

    @abstractmethod
    def fetch[T: Transport](self: T) -> "FetchConnection[T]":
        """
        Initialize the connection to the remote service (git-upload-pack or git-receive-pack).
        """
        pass

    @abstractmethod
    def push[T: Transport](self: T) -> "PushConnection[T]":
        """
        Initialize the connection to the remote service (git-upload-pack or git-receive-pack).
        """
        pass

    @property
    def url(self) -> str:
        return self._url

    async def get_credentials(self, origin: str) -> tuple[str, str] | None:
        return await self.credentials_provider.get_credentials(origin)

    @classmethod
    @abstractmethod
    def can_handle_url(cls, url: str) -> bool:
        """
        Check if this transport can handle the given URL.
        """
        return False

    @classmethod
    def from_url(cls, url: str, *, credentials_provider: CredentialProvider) -> "Transport":
        """
        Create a transport instance from a URL.
        """
        assert credentials_provider is not None, "Credentials provider is required"
        for cls in cls.__subclasses__():
            if cls.can_handle_url(url):
                return cls(url=url, credentials_provider=credentials_provider)
        raise ValueError(f"Unsupported URL: {url}")


class BaseConnection[T: Transport]:
    capabilities: frozenset[str]
    transport: T
    reader: StreamReader | None
    writer: StreamWriter | None
    git_protocol: int | None

    def __init__(self, *, transport: T):
        self.transport = transport
        self.capabilities = frozenset()
        self.reader = None
        self.writer = None

        self.git_protocol = None
        self._negotiated_protocol = None
        self.last_packet: PacketLine | None = None

        self._shifted_packets: list[PacketLine] = []

    @property
    def negotiated_protocol(self) -> int | None:
        if self._negotiated_protocol is not None:
            return self._negotiated_protocol
        return self.git_protocol

    @abstractmethod
    async def _close_service_connection(self) -> None:
        """
        Close the connection to the remote service.
        """
        pass

    async def _read_packet(self) -> PacketLine:
        assert self.reader is not None

        if self._shifted_packets:
            # There are packets that were shifted, return the first one
            return self._shifted_packets.pop(0)

        pkt_marker = await self.reader.readexactly(4)
        pkt_type, payload_length = PacketLineType.from_bytes(pkt_marker)

        if pkt_type == PacketLineType.DATA:
            pkt_payload = await self.reader.readexactly(payload_length)
            return PacketLine.from_marker_and_payload(pkt_type, pkt_payload)
        else:
            return PacketLine.from_marker_and_payload(pkt_type, None)

    def _at_eof(self):
        return not self._shifted_packets and (self.reader is None or self.reader.at_eof())

    def _shift_packet(self, packet: PacketLine) -> None:
        self._shifted_packets.append(packet)

    async def _read_packets_until_flush(self) -> AsyncIterator[PacketLine]:
        assert self.reader is not None

        while not self._at_eof():
            try:
                packet = await self._read_packet()
                if packet.type == PacketLineType.FLUSH:
                    self.last_packet = packet
                    return
                yield packet
            except IncompleteReadError as e:
                if len(e.partial) > 0:
                    raise ValueError(f"Unexpected EOF while reading packet length: {e.partial}")
                raise ValueError("Reached EOF before FLUSH packet") from e

    async def _read_packets_section(self) -> AsyncIterator[PacketLine]:
        """
        Read packets until a either delimiter or flush packet is received.

        The caller can check which packet was received by looking at the last_packet attribute after the iteration is done.
        """
        while True:
            try:
                packet = await self._read_packet()
                if packet.type in (PacketLineType.FLUSH, PacketLineType.DELIMITER):
                    self.last_packet = packet
                    return
                yield packet
            except IncompleteReadError as e:
                if len(e.partial) > 0:
                    raise ValueError(f"Unexpected EOF while reading packet length: {e.partial}")
                raise ValueError("Reached EOF before FLUSH packet") from e

    async def _write_packet(self, packet: PacketLine) -> None:
        assert self.writer is not None

        self.writer.write(packet.marker_bytes)
        self.writer.write(packet.data)
        await self.writer.drain()

    async def _read_header_packet(self, section: AsyncIterator[PacketLine]) -> str:
        header = await anext(section)
        if header.type != PacketLineType.DATA:
            raise ValueError(f"Unexpected packet type: {header.type}: {header.data}")
        return header.data.decode("ascii").rstrip()

    async def _read_v1_server_hello(self) -> tuple[dict[str, str], frozenset[str]]:
        """
        Process the first ref packet received from the server.

        The first ref packet is special as it contains the capabilities of the server.
        """
        assert self.negotiated_protocol == 1

        refs: dict[str, str] = {}

        # Read the capabilities from the server (https://git-scm.com/docs/protocol-v2#_capability_advertisement)
        section = self._read_packets_section()
        version_data = await self._read_header_packet(section)

        # In V1, the version packet is optional
        if version_data.startswith("version "):
            if version_data != "version 1":
                raise ValueError(f"Expected 'version 1' packet, instead got: {version_data}")
            # read next packet
            first_ref_extended = await self._read_header_packet(section)
        else:
            first_ref_extended = version_data

        first_ref_data, capabilities_list = first_ref_extended.split("\x00", 1)
        first_ref = Ref.from_line(first_ref_data)
        refs[first_ref.name] = first_ref.object_id

        async for packet in section:
            assert packet.type == PacketLineType.DATA
            ref = Ref.from_line(packet.data.decode("ascii").rstrip())
            refs[ref.name] = ref.object_id

        return refs, frozenset(capabilities_list.split(" "))

    def _add_capability_if_supported(self, in_use: set[str], capability: str) -> bool:
        if capability in self.capabilities:
            in_use.add(capability)
            return True
        return False


class FetchConnection[T: Transport](BaseConnection[T]):
    """
    Git Protocol V2 fetch connection.

    Supported operations:

    - List references via ```#ls_refs()```
    - Fetch objects via ```#fetch_objects()```

    """

    def __init__(self, *, transport: T):
        super().__init__(transport=transport)

        self.git_protocol = 2
        self._negotiated_protocol = None

    @abstractmethod
    async def _open_fetch_service_connection(self) -> tuple[StreamReader, StreamWriter]:
        """
        Connect to the remote service and return the reader and writer.
        """
        pass

    async def __aenter__(self) -> "FetchConnection[T]":
        assert self.reader is None
        assert self.writer is None

        self.reader, self.writer = await self._open_fetch_service_connection()

        if self.negotiated_protocol == 2:
            await self._process_hello_v2()
        elif self.negotiated_protocol == 1:
            await self._process_hello_v1()

        logger.debug(f"Connected with capabilities: {self.capabilities}")

        # We expect the server to send us the capabilities and end with a flush packet
        return self

    async def _process_hello_v2(self):
        """
        Process the hello packet from the server when using Git Protocol V2.
        """
        version_packet = await self._read_packet()
        if version_packet.data.strip() != b"version 2":
            logger.error(f"Expected 'version 2' packet, instead got: {version_packet.data}")
            raise ValueError(f"Failed to open fetch connection to {self.transport.url}")

        # Read the capabilities from the server (https://git-scm.com/docs/protocol-v2#_capability_advertisement)
        advertised_capabilities: set[str] = set()
        async for packet in self._read_packets_until_flush():
            assert packet.type == PacketLineType.DATA
            advertised_capabilities.add(packet.data.decode("ascii").rstrip())

        self.capabilities = frozenset(advertised_capabilities)

    async def _process_hello_v1(self):
        """
        Process the hello packet from the server when using Git Protocol V1.
        """
        refs, advertised_capabilities = await self._read_v1_server_hello()

        self.capabilities = frozenset(advertised_capabilities)
        self._cached_refs = refs

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:  # type: ignore
        if self.writer:
            logger.debug("Closing writer")
            await self._write_packet(PacketLine.FLUSH)
            self.writer.write_eof()
            await self.writer.drain()
            self.writer.close()
            self.writer = None

        if self.reader:
            logger.debug("Closing reader")
            self.reader.feed_eof()
            self.reader = None

        await self._close_service_connection()

    async def _process_ack_section(self, ack_section: AsyncIterator[PacketLine], missing_objects: set[str]):
        async for ack in ack_section:
            if ack.data == b"nak\n":
                break
            if ack.data.startswith(b"ack\0"):
                obj_id = ack.data[4:].decode("ascii").strip()
                try:
                    missing_objects.remove(obj_id)
                except KeyError:
                    logger.warning(f"Received ACK for unknown object: {obj_id}")
            if ack.data == b"ready\n":
                break

        assert self.last_packet, "Expected last packet to be set"
        if self.last_packet.type == PacketLineType.FLUSH:
            raise ConnectionException("Server negotiation failed. Missing objects: %s" % (missing_objects,))

    async def _generate_command_v2(
        self,
        command: str,
        args: Iterable[str],
        **capabilities: dict[str, str],
    ) -> AsyncIterator[PacketLine]:
        """
        Send a command to the server with the given arguments and capabilities using GitProtocol V2.

        See: https://git-scm.com/docs/gitprotocol-v2#_command_request
        """
        # Send command
        yield PacketLine.data_from_string(f"command={command}")
        # Send capabilities
        for key, value in capabilities.items():
            data = f"{key}={value}" if len(value) > 0 else key
            yield PacketLine.data_from_string(data)

        yield PacketLine.DELIMITER

        # send arguments
        for arg in args:
            yield PacketLine.data_from_string(arg)

        yield PacketLine.FLUSH

    async def _send_packet_transaction(self, packets: AsyncIterator[PacketLine]) -> None:
        """
        Send a series of packets to the server.

        This is a hook method that can be overridden by subclasses to implement custom behavior.
        """
        assert self.writer is not None
        async for packet in packets:
            await self._write_packet(packet)

    async def _send_command_v2(
        self,
        command: str,
        args: Iterable[str],
        **capabilities: dict[str, str],
    ) -> None:
        await self._send_packet_transaction(self._generate_command_v2(command, args, **capabilities))

    async def ls_refs(self, prefix: str = "") -> AsyncIterator[Ref]:
        """
        List references on the remote server.

        For V1, the refs are cached from the advertisement packet.
        For V2: https://git-scm.com/docs/gitprotocol-v2#_ls_refs
        """
        if self.negotiated_protocol == 1:
            assert self._cached_refs is not None

            logger.debug("Using cached refs from advertisement: %s", self._cached_refs)

            # Used cached refs from advertisement
            for name, obj_id in self._cached_refs.items():
                if prefix and not name.startswith(prefix):
                    continue
                yield Ref(name, obj_id)
            return

        assert self.negotiated_protocol == 2

        args: list[str] = []
        if prefix:
            args.append(f"ref-prefix {prefix}")
        # Send the command
        await self._send_command_v2("ls-refs", args)

        # Read the response
        async for packet in self._read_packets_until_flush():
            assert packet.type == PacketLineType.DATA
            ref = Ref.from_line(packet.data.decode("ascii").rstrip())
            if prefix and not ref.name.startswith(prefix):
                continue
            yield ref

    async def fetch_objects(
        self,
        objects: set[str],
        *,
        have: set[str] | None = None,
        output: AsyncBufferedIOBase,
    ) -> None:
        """
        Fetch objects from the remote server.
        """
        if self.negotiated_protocol == 1:
            return await self._fetch_objects_v1(objects, have=have, output=output)

        if self.negotiated_protocol == 2:
            return await self._fetch_objects_v2(objects, have=have, output=output)

        raise ConnectionException("Unsupported protocol version: %s" % self.negotiated_protocol)

    async def _generate_fetch_v1_request(self, want: set[str], have: set[str] | None) -> AsyncIterator[PacketLine]:
        capabilities: set[str] = set()
        capabilities.add("agent=kalandra")

        self._add_capability_if_supported(capabilities, "multi_ack_detailed")

        # TODO: support side-band in v1
        # if not self._add_capability_if_supported(capabilities, "side-band-64k"):
        #     self._add_capability_if_supported(capabilities, "side-band")

        assert len(want) >= 1, "Expected at least one object to fetch"
        want_iter = iter(want)

        # Send first want + capabilities
        obj = next(want_iter)
        yield PacketLine.data_from_string(f"want {obj} {' '.join(sorted(capabilities))}")

        # Send the rest of the wants
        for obj in want_iter:
            yield PacketLine.data_from_string("want %s" % obj)

        # Send the oids of objects we have
        if have:
            for obj in have:
                # Make sure we don't send NULL_OBJECT_ID by mistake
                if obj == NULL_OBJECT_ID:
                    continue
                yield PacketLine.data_from_string("have %s" % obj)

        # Send done
        yield PacketLine.FLUSH
        yield PacketLine.data_from_string("done")

    async def _fetch_objects_v1(
        self,
        objects: set[str],
        *,
        have: set[str] | None = None,
        output: AsyncBufferedIOBase,
    ) -> None:
        """
        (Git Protocol V1) Fetch objects from the remote server.

        Spec:

        -   https://git-scm.com/docs/http-protocol#_smart_service_git_upload_pack
        -   https://git-scm.com/docs/pack-protocol#_packfile_negotiation
        """
        await self._send_packet_transaction(self._generate_fetch_v1_request(objects, have))

        assert self.reader is not None

        pkt = await self._read_packet()
        while pkt.data.startswith(b"ACK "):
            # we will zero or more ACKs - we don't care about them
            pkt = await self._read_packet()

        if pkt.data.rstrip() != b"NAK":
            raise ConnectionError("Expected NAK packet, instead got: %r" % pkt.data)

        # The rest of the response is the packfile
        async for chunk in self.reader:
            await output.write(chunk)

        await output.flush()

    async def _fetch_objects_v2(
        self,
        objects: set[str],
        *,
        have: set[str] | None = None,
        output: AsyncBufferedIOBase,
    ) -> None:
        """
        (Git Protocol V2) Fetch objects from the remote server.

        Spec: https://git-scm.com/docs/gitprotocol-v2#_fetch
        """
        # Send the command
        base_args = ()
        if "wait-for-done" in self.capabilities:
            base_args += ("wait-for-done",)

        have_args = ("have " + obj for obj in have) if have else ()
        want_args = ("want " + obj for obj in objects)
        await self._send_command_v2("fetch", args=itertools.chain(base_args, have_args, want_args, ("done",)))

        # NOTE: we always send the "done" immediately not waiting for the server to send us the acks
        #       as we can't really do anything with missing objects anyway

        # The response of fetch is broken into a number of sections separated by delimiter packets (0001),
        # with each section beginning with its section header. Most sections are sent only when the packfile is sent.

        # output = acknowledgements flush-pkt |
        #     [acknowledgments delim-pkt] [shallow-info delim-pkt]
        #     [wanted-refs delim-pkt] [packfile-uris delim-pkt]
        #     packfile flush-pkt

        ## 1. acknowledgements flush-pkt | [acknowledgments delim-pkt]
        missing_objects: set[str] = set(objects)

        section = self._read_packets_section()
        header_name = await self._read_header_packet(section)

        if header_name == "acknowledgments":
            await self._process_ack_section(section, missing_objects)
            # after done, missing_objects contains all non-acked objects

            logger.info(f"Did not receive ACKs for {len(missing_objects)} objects")

            # read the next section
            section = self._read_packets_section()
            header_name = await self._read_header_packet(section)

        ## 2. We don't request shallow-info, so we skip it
        ## 3. We don't request wanted-refs, so we skip it
        ## 4. We don't request packfile-uris, so we skip it

        # 5. packfile flush-pkt
        if header_name != "packfile":
            raise ConnectionException(f"Unexpected section: {header_name}")

        async for packet in section:
            assert packet.type == PacketLineType.DATA
            stream_code = packet.data[0]
            if stream_code == 1:
                await output.write(memoryview(packet.data)[1:])
            elif stream_code == 2:
                msg = packet.data[1:].decode("utf-8")
                logger.info(msg)
            elif stream_code == 3:
                msg = packet.data[1:].decode("utf-8")
                logger.error(msg)

        assert self.last_packet, "Expected last packet to be set"
        if self.last_packet.type != PacketLineType.FLUSH:  # type: ignore
            logger.warning(f"Unexpected packet type at end of packfile: {self.last_packet.type}")


class PushConnection[T: Transport](BaseConnection[T]):
    """
    https://git-scm.com/docs/gitprotocol-pack#_pushing_data_to_a_server
    """

    def __init__(self, *, transport: T):
        super().__init__(transport=transport)

        # git-receive-pack does not support protocol v2 yet, so make sure we use v1
        self.git_protocol = 1
        self.refs = {}

    @abstractmethod
    async def _open_push_service_connection(self) -> tuple[StreamReader, StreamWriter]:
        """
        Connect to the remote service and return the reader and writer.
        """
        pass

    async def __aenter__(self) -> "PushConnection[T]":
        assert self.reader is None
        assert self.writer is None

        self.reader, self.writer = await self._open_push_service_connection()

        # Read the first ref packet, which is special as it will contain the capabilities
        self.refs, self.capabilities = await self._read_v1_server_hello()

        logger.debug(f"Connected with capabilities: {self.capabilities}")

        # We expect the server to send us the capabilities and end with a flush packet
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:  # type: ignore
        if self.reader:
            logger.debug("Closing reader")
            self.reader.feed_eof()
            self.reader = None

        if self.writer:
            logger.debug("Closing writer")
            try:
                await self._write_packet(PacketLine.FLUSH)
                self.writer.write_eof()
                self.writer.close()
            except ConnectionResetError:
                logger.debug("Connection was reset")
            self.writer = None

        await self._close_service_connection()

    async def _generate_receive_commands(
        self,
        changes: list[RefChange],
        supports_delete: bool,
        use_capabilties: set[str],
    ) -> AsyncIterator[PacketLine]:
        first = True

        for change in changes:
            if change.is_delete:
                if not supports_delete:
                    logger.warning(f"Server does not support delete-refs capability, skipping delete of {change.ref}")
                    continue

            line = f"{change.old} {change.new} {change.ref}"
            if first:
                line += "\0 " + " ".join(use_capabilties)
                first = False
            yield PacketLine.data_from_string(line)

        # We need to send flush packet at the end of commands. THIS IS MISSING FROM DOCS!
        yield PacketLine.FLUSH

    async def _push_request_v1(
        self, changes: list[RefChange], packfile: AsyncBufferedIOBase | None
    ) -> tuple[frozenset[str], AsyncIterator[PacketLine], AsyncBufferedIOBase | None]:
        """
        Prepare the push request
        """
        use_capabilties: set[str] = set()

        # add any report-status capability if supported
        self._add_capability_if_supported(use_capabilties, "report-status")
        self._add_capability_if_supported(use_capabilties, "side-band-64k")
        self._add_capability_if_supported(use_capabilties, "object-format=sha1")
        use_capabilties.add("agent=git/2.46.00000")
        supports_delete = "delete-refs" in self.capabilities

        packets = self._generate_receive_commands(changes, supports_delete, use_capabilties)
        # has_non_deletes = any(not change.is_delete for change in changes)

        return frozenset(use_capabilties), packets, packfile  # if has_non_deletes else None

    async def _send_packfile(self, packfile: AsyncBufferedIOBase) -> None:
        assert self.writer is not None
        expected_total = await packfile.seek(0, 2)
        await packfile.seek(0)

        bcount = 0
        total = 0
        async for chunk in packfile:
            self.writer.write(chunk)
            bcount += len(chunk)
            if bcount >= (10 * MEGABYTES):
                await self.writer.drain()
                total += bcount
                bcount = 0
                logger.debug("Sent %.1f/%.1f MB", total / MEGABYTES, expected_total / MEGABYTES)

        await self.writer.drain()
        logger.debug("Packfile sent. Waiting for server response")

    async def _send_commands(self, packets: AsyncIterator[PacketLine], packfile: AsyncBufferedIOBase | None) -> None:
        assert self.writer is not None

        async for packet in packets:
            await self._write_packet(packet)

        if packfile is not None:
            await self._send_packfile(packfile)

    async def push_changes(self, changes: list[RefChange], packfile: AsyncBufferedIOBase | None) -> None:
        """
        (Git Protocol V1) Send changes to the remote server.

        @see: https://git-scm.com/docs/gitprotocol-pack#_pushing_data_to_a_server
        """
        used_capabilities, packets, packfile_to_send = await self._push_request_v1(changes, packfile)

        await self._send_commands(packets, packfile_to_send)

        # Try to read the report-status
        if "report-status" in used_capabilities:
            logger.info("Reading report-status")
            async for packet in self._read_packets_until_flush():
                assert packet.type == PacketLineType.DATA
                channel = int(packet.data[0])

                if channel == 2:
                    logger.info("[%d] %r", channel, packet.data.decode("utf-8", "replace"))
                if channel == 1:
                    message = PacketLine.from_buffer(packet.data[1:]).data.decode("utf-8", "replace").strip()
                    logger.info("[%d] %s", channel, message)
            logger.info("Report-status completed")

        logger.info("Push completed")
