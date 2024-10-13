"""
Tools related to git protocol v2.

See: https://git-scm.com/docs/protocol-v2

"""

from collections.abc import Buffer
from enum import Enum
from typing import NamedTuple


class PacketLineType(Enum):
    DATA = -1
    FLUSH = 0
    DELIMITER = 1
    RESPONSE_END = 2
    UNKNOWN = 3

    @classmethod
    def from_bytes(cls, data: bytes) -> "tuple[PacketLineType, int]":
        if len(data) != 4:
            raise ValueError("Invalid packet line marker size: %r" % len(data))
        pkt_marker = int(data, 16)
        if pkt_marker >= 4:
            return cls.DATA, pkt_marker - 4
        else:
            return cls(pkt_marker), 0


class PacketLine:
    """
    Represents a single packet line in the git protocol.

    See: https://git-scm.com/docs/gitprotocol-common#_pkt_line_format
    """

    __slots__ = ("length", "data", "type")

    length: int
    data: bytes
    type: PacketLineType

    FLUSH: "PacketLine"
    DELIMITER: "PacketLine"

    @classmethod
    def from_buffer(cls, data: Buffer) -> "PacketLine":
        pkt_type, pkt_payload_length, pkt_payload = cls.sniff_buffer(data)
        if pkt_type is None:
            raise ValueError("Not enough data to determine packet type")
        if pkt_payload_length < 0:
            raise ValueError("Not enough data to read the whole packet, need %d more bytes" % -pkt_payload_length)

        return cls.from_marker_and_payload(pkt_type, pkt_payload)

    @classmethod
    def from_marker_and_payload(cls, pkt_type: PacketLineType, payload: Buffer | None) -> "PacketLine":
        if pkt_type != PacketLineType.DATA:
            return cls(0, b"", pkt_type)
        else:
            if payload is None:
                raise ValueError("Payload is required for DATA packet type")
            mview = memoryview(payload)
            return cls(len(mview), bytes(mview), pkt_type)

    @classmethod
    def data_from_string(cls, data: str) -> "PacketLine":
        encoded = f"{data}\n".encode("ascii")
        return cls(len(encoded), encoded, PacketLineType.DATA)

    @classmethod
    def sniff_buffer(cls, data: Buffer) -> "tuple[PacketLineType | None, int, Buffer | None]":
        """
        Sniff the buffer to determine the type of packet line and its length.

        If there is not enough data to determine the type, returns None and a negative number
        indicating the number of bytes missing to determine the type.

        If there is enough data to determine the type, but not enough to read the whole payload,
        returns the type and a negative number indicating the number of bytes missing to read the whole payload.

        If there is enough data to determine the type and read the whole packet,
        returns the type and the length of the packet's payload.
        """
        mview = memoryview(data)
        mview_length = len(mview)
        if mview_length < 4:
            # Not enough data to read the length
            return None, 4 - mview_length, None

        pkt_marker = int(bytes(mview[:4]), 16)
        if pkt_marker >= 4:
            if pkt_marker > len(mview):
                # we know the length, but we don't have enough data to read the whole packet
                return PacketLineType.DATA, len(mview) - pkt_marker, None
            return PacketLineType.DATA, pkt_marker - 4, mview[4:pkt_marker]
        else:
            return PacketLineType(pkt_marker), 0, None

    def __init__(self, length: int, data: bytes, type: PacketLineType):
        self.length = length
        self.data = data
        self.type = type

    @property
    def marker_bytes(self) -> bytes:
        return b"%04x" % (self.length + 4 if self.type == PacketLineType.DATA else self.type.value)

    def __repr__(self) -> str:
        if self.length < 100:
            return f"PacketLine({self.length}, {self.data!r}, {self.type})"
        else:
            return f"PacketLine({self.length}, {self.data[:100]!r}..., {self.type})"


PacketLine.FLUSH = PacketLine.from_marker_and_payload(PacketLineType.FLUSH, None)
PacketLine.DELIMITER = PacketLine.from_marker_and_payload(PacketLineType.DELIMITER, None)


NULL_OBJECT_ID = "0" * 40


class Ref(NamedTuple):
    name: str
    object_id: str

    @classmethod
    def from_line(cls, line: str) -> "Ref":
        parts = line.split(" ")
        if len(parts) != 2:
            raise ValueError(f"Invalid ref line: {line}")
        return cls(name=parts[1], object_id=parts[0])


class RefChange(NamedTuple):
    ref: str
    old: str
    new: str

    def __str__(self) -> str:
        if self.is_create:
            return f"CREATE {self.ref} {self.new}"
        elif self.is_delete:
            return f"DELETE {self.ref} {self.old}"
        else:
            return f"UPDATE {self.ref} {self.old}..{self.new}"

    @property
    def is_delete(self) -> bool:
        return self.new == NULL_OBJECT_ID

    @property
    def is_create(self) -> bool:
        return self.old == NULL_OBJECT_ID

    @property
    def is_update(self) -> bool:
        return self.old != NULL_OBJECT_ID and self.new != NULL_OBJECT_ID
