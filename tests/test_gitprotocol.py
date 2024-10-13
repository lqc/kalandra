import pytest
from kalandra.gitprotocol import PacketLine, PacketLineType


@pytest.mark.parametrize(
    "data, expected_length, expected_payload",
    [
        (b"0006a\n", 2, b"a\n"),
        (b"0005a", 1, b"a"),
        (b"000bfoobar\n", 7, b"foobar\n"),
    ],
)
def test_packetline_create_data_packet(
    data: bytes, expected_length: int, expected_payload: bytes
):  # type: ignore
    line = PacketLine.from_buffer(data)

    assert line.length == expected_length
    assert line.data == expected_payload
    assert line.type == PacketLineType.DATA


@pytest.mark.parametrize(
    "data, type",
    [
        (b"0000", PacketLineType.FLUSH),
        (b"0001", PacketLineType.DELIMITER),
        (b"0002", PacketLineType.RESPONSE_END),
        (b"0003", PacketLineType.UNKNOWN),
    ],
)
def test_packetline_special_types(data: bytes, type: PacketLineType):  # type: ignore
    line = PacketLine.from_buffer(data)

    assert line.length == 0
    assert line.data == b""
    assert line.type == type


def test_packetline_too_long():
    with pytest.raises(ValueError):
        PacketLine.from_buffer(b"ffff0000")


def test_packetline_too_short():
    with pytest.raises(ValueError):
        PacketLine.from_buffer(b"0a0")


def test_packetline_create_with_offset():
    line = PacketLine.from_buffer(b"xxx0007ABC", offset=3)

    assert line.length == 3
    assert line.data == b"ABC"
    assert line.type == PacketLineType.DATA


def test_packetline_create_with_length_exceeding_buffer():
    with pytest.raises(ValueError):
        PacketLine.from_buffer(b"0006X")
