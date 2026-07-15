import pytest

from isotp_frame import (
    parse_isotp_frame,
    SingleFrame,
    FirstFrame,
    ConsecutiveFrame,
    FlowControlFrame,
    FlowStatus,
)


# 기능 그룹: ISO-TP 데이터 프레임 파싱
# 검증 목적: Single, First, Consecutive Frame의 길이와 payload 필드가 올바르게 해석되는지 확인한다.
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


# 기능 그룹: ISO-TP 흐름 제어 프레임 파싱
# 검증 목적: Flow Control 필드와 CTS, WAIT, OVERFLOW 상태가 규격에 맞게 해석되는지 확인한다.
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
    frame = parse_isotp_frame(bytes.fromhex(raw_data))

    assert isinstance(frame, FlowControlFrame)
    assert frame.flow_status == expected_status


# 기능 그룹: 잘못된 ISO-TP 프레임 입력 검증
# 검증 목적: 불완전하거나 지원하지 않는 프레임과 유효하지 않은 Flow Status를 명확한 예외로 거부하는지 확인한다.
@pytest.mark.parametrize(
    ("raw_data", "expected_message"),
    [
        ("", "Empty CAN data"),
        ("00", "Single Frame payload length must be positive"),
        ("04 10 01", "Single Frame payload length mismatch"),
        ("10", "First Frame requires at least 2 bytes"),
        ("30 00", "Flow Control Frame requires at least 3 bytes"),
        ("40 00 00", "Unsupported ISO-TP frame type: 4"),
    ],
)
def test_parse_invalid_isotp_frame_raises_value_error(raw_data, expected_message):
    with pytest.raises(ValueError, match=expected_message):
        parse_isotp_frame(bytes.fromhex(raw_data))


def test_invalid_flow_status_raises_value_error():
    data = bytes.fromhex("33 00 00 00 00 00 00 00")

    with pytest.raises(ValueError, match="Invalid Flow Status: 3"):
        parse_isotp_frame(data)
