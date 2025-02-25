import asyncio
from typing import Coroutine


class BytesStreamWriter:
    """
    Mimick interface of StreamWriter, but write to a buffer instead of a socket.
    """

    def __init__(self, drain_func: Coroutine[None, bytearray, None] | None) -> None:
        self._buffer = bytearray()
        self._is_eof = False
        self._drain_func = drain_func

    def write(self, data: bytes) -> None:
        assert not self._is_eof, "Cannot write to a closed stream"
        self._buffer.extend(data)

    async def drain(self) -> None:
        if not self._drain_func or self._buffer:
            return

        self._drain_func.send(self._buffer)
        self._buffer.clear()

    def can_write_eof(self) -> bool:
        return True

    def write_eof(self) -> None:
        self._is_eof = True

    async def wait_closed(self) -> None:
        pass

    def getvalue(self) -> bytes:
        return bytes(self._buffer)


class BytesStreamReader(asyncio.StreamReader):
    """
    StreamReader with all the data pre-fetched from a memory buffer.
    """

    def __init__(self, data: bytes) -> None:
        super().__init__()
        self.feed_data(data)
        self.feed_eof()
