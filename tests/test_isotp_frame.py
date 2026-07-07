import pytest

from isotp_frame import (
    parse_isotp_frame,
    SingleFrame,
    FirstFrame,
    ConsecutiveFrame,
    FlowControlFrame,
    FlowStatus,
)


def test_parse_single_frame():
    data = bytes.fromhex("02 10 01 00 00 00 00 00")

    frame = parse_isotp_frame(data)

    assert isinstance(frame, SingleFrame)
    assert frame.length == 2
    assert frame.payload == bytes.fromhex("10 01")


def test_parse_first_frame():
    data = bytes.fromhex("10 14 36 01 AA BB CC DD")

    frame = parse_isotp_frame(data)

    assert isinstance(frame, FirstFrame)
    assert frame.total_length == 0x14
    assert frame.payload == bytes.fromhex("36 01 AA BB CC DD")


def test_parse_consecutive_frame():
    data = bytes.fromhex("21 EE FF 00 11 22 33 44")

    frame = parse_isotp_frame(data)

    assert isinstance(frame, ConsecutiveFrame)
    assert frame.sequence_number == 1
    assert frame.payload == bytes.fromhex("EE FF 00 11 22 33 44")


def test_parse_flow_control_frame():
    data = bytes.fromhex("30 00 00 00 00 00 00 00")

    frame = parse_isotp_frame(data)

    assert isinstance(frame, FlowControlFrame)
    assert frame.flow_status == FlowStatus.CONTINUE_TO_SEND
    assert frame.block_size == 0
    assert frame.st_min == 0


@pytest.mark.parametrize(
    ("raw_data", "expected_status"),
    [
        ("30 00 00 00 00 00 00 00", FlowStatus.CONTINUE_TO_SEND),
        ("31 08 0A 00 00 00 00 00", FlowStatus.WAIT),
        ("32 00 00 00 00 00 00 00", FlowStatus.OVERFLOW),
    ],
)
def test_parse_flow_control_status(raw_data, expected_status):
    # Flow Status 값 0, 1, 2가 각각 CTS, WAIT, OVERFLOW로 해석되는지 확인.
    frame = parse_isotp_frame(bytes.fromhex(raw_data))

    assert isinstance(frame, FlowControlFrame)
    assert frame.flow_status == expected_status


@pytest.mark.parametrize(
    ("raw_data", "expected_message"),
    [
        ("", "Empty CAN data"),
        ("04 10 01", "Single Frame payload length mismatch"),
        ("10", "First Frame requires at least 2 bytes"),
        ("30 00", "Flow Control Frame requires at least 3 bytes"),
        ("40 00 00", "Unsupported ISO-TP frame type: 4"),
    ],
)
def test_parse_invalid_isotp_frame_raises_value_error(raw_data, expected_message):
    # 파싱할 수 없는 ISO-TP 입력은 조용히 통과시키지 않고 ValueError로 실패해야 함.
    with pytest.raises(ValueError, match=expected_message):
        parse_isotp_frame(bytes.fromhex(raw_data))


def test_invalid_flow_status_raises_value_error():
    # Flow Status 3 이상은 ISO-TP에서 정의한 상태가 아니므로 예외로 처리.
    data = bytes.fromhex("33 00 00 00 00 00 00 00")

    with pytest.raises(ValueError, match="Invalid Flow Status: 3"):
        parse_isotp_frame(data)
