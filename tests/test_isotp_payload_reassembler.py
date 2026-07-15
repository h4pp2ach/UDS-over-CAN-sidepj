import pytest

from isotp_frame import (
    ConsecutiveFrame,
    FirstFrame,
    FlowControlFrame,
    FlowStatus,
    SingleFrame,
)
from isotp_payload_reassembler import IsoTpPayloadReassembler


# 기능 그룹: 정상 ISO-TP payload 재조립
# 검증 목적: Single Frame과 Multi Frame payload를 완성하고 마지막 프레임의 padding을 제거하는지 확인한다.
def test_reassemble_single_frame_returns_payload_immediately():
    reassembler = IsoTpPayloadReassembler()

    payload = reassembler.feed(SingleFrame(length=3, payload=bytes.fromhex("10 01 AA")))

    assert payload == bytes.fromhex("10 01 AA")
    assert reassembler.is_in_progress is False


def test_reassemble_multi_frame_payload():
    reassembler = IsoTpPayloadReassembler()

    assert reassembler.feed(FirstFrame(total_length=21, payload=bytes.fromhex("01 02 03 04 05 06"))) is None
    assert reassembler.feed(ConsecutiveFrame(sequence_number=1, payload=bytes.fromhex("07 08 09 0A 0B 0C 0D"))) is None
    assert reassembler.feed(ConsecutiveFrame(sequence_number=2, payload=bytes.fromhex("0E 0F 10 11 12 13 14"))) is None

    payload = reassembler.feed(
        ConsecutiveFrame(sequence_number=3, payload=bytes.fromhex("15 00 00 00 00 00 00"))
    )

    assert payload == bytes(range(1, 22))
    assert reassembler.is_in_progress is False


def test_reassemble_trims_last_consecutive_frame_padding():
    reassembler = IsoTpPayloadReassembler()

    assert reassembler.feed(FirstFrame(total_length=8, payload=bytes.fromhex("01 02 03 04 05 06"))) is None

    payload = reassembler.feed(
        ConsecutiveFrame(sequence_number=1, payload=bytes.fromhex("07 08 AA BB CC DD EE"))
    )

    assert payload == bytes.fromhex("01 02 03 04 05 06 07 08")


# 기능 그룹: 재조립 순서와 상태 오류 처리
# 검증 목적: 잘못된 프레임 순서를 예외로 거부하고 진행 중인 조립 상태를 안전하게 초기화하는지 확인한다.
def test_single_frame_during_multi_frame_raises_value_error_and_resets():
    reassembler = IsoTpPayloadReassembler()
    reassembler.feed(FirstFrame(total_length=8, payload=bytes.fromhex("01 02 03 04 05 06")))

    with pytest.raises(ValueError, match="Single Frame received while multi-frame payload is in progress"):
        reassembler.feed(SingleFrame(length=2, payload=bytes.fromhex("10 01")))

    assert reassembler.is_in_progress is False


def test_consecutive_frame_before_first_frame_raises_value_error():
    reassembler = IsoTpPayloadReassembler()

    with pytest.raises(ValueError, match="Consecutive Frame received before First Frame"):
        reassembler.feed(ConsecutiveFrame(sequence_number=1, payload=bytes.fromhex("01 02")))


def test_unexpected_sequence_number_raises_value_error_and_resets():
    reassembler = IsoTpPayloadReassembler()
    reassembler.feed(FirstFrame(total_length=8, payload=bytes.fromhex("01 02 03 04 05 06")))

    with pytest.raises(ValueError, match="Unexpected sequence number: expected 1, got 2"):
        reassembler.feed(ConsecutiveFrame(sequence_number=2, payload=bytes.fromhex("07 08")))

    assert reassembler.is_in_progress is False


def test_empty_consecutive_frame_payload_raises_value_error_and_resets():
    reassembler = IsoTpPayloadReassembler()
    reassembler.feed(FirstFrame(total_length=8, payload=bytes.fromhex("01 02 03 04 05 06")))

    with pytest.raises(ValueError, match="Consecutive Frame payload must not be empty"):
        reassembler.feed(ConsecutiveFrame(sequence_number=1, payload=b""))

    assert reassembler.is_in_progress is False


def test_invalid_new_first_frame_resets_in_progress_message():
    reassembler = IsoTpPayloadReassembler()
    reassembler.feed(FirstFrame(total_length=8, payload=bytes.fromhex("01 02 03 04 05 06")))

    with pytest.raises(ValueError, match="First Frame received while multi-frame payload is in progress"):
        reassembler.feed(FirstFrame(total_length=9, payload=bytes.fromhex("AA BB CC DD EE FF")))

    assert reassembler.is_in_progress is False


def test_flow_control_frame_raises_value_error():
    reassembler = IsoTpPayloadReassembler()

    with pytest.raises(ValueError, match="Flow Control Frame cannot be reassembled as payload"):
        reassembler.feed(
            FlowControlFrame(
                flow_status=FlowStatus.CONTINUE_TO_SEND,
                block_size=0,
                st_min=0,
            )
        )


# 기능 그룹: 재조립 입력 값과 타입 검증
# 검증 목적: 모순된 프레임 필드와 지원하지 않는 객체를 명확한 예외로 거부하는지 확인한다.
@pytest.mark.parametrize(
    ("frame", "expected_message"),
    [
        (SingleFrame(length=0, payload=b""), "Single Frame length must be positive"),
        (SingleFrame(length=3, payload=bytes.fromhex("10 01")), "Single Frame length does not match payload length"),
        (FirstFrame(total_length=0, payload=bytes.fromhex("01 02")), "First Frame total length must be positive"),
        (FirstFrame(total_length=8, payload=b""), "First Frame payload must not be empty"),
        (
            FirstFrame(total_length=2, payload=bytes.fromhex("01 02")),
            "First Frame payload must be shorter than total length",
        ),
    ],
)
def test_invalid_frame_state_raises_value_error(frame, expected_message):
    reassembler = IsoTpPayloadReassembler()

    with pytest.raises(ValueError, match=expected_message):
        reassembler.feed(frame)


def test_unsupported_frame_object_raises_type_error():
    reassembler = IsoTpPayloadReassembler()

    with pytest.raises(TypeError, match="Unsupported ISO-TP frame object: object"):
        reassembler.feed(object())
