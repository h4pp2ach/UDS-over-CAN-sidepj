import pytest

from isotp_frame import (
    ConsecutiveFrame,
    FirstFrame,
    FlowControlFrame,
    FlowStatus,
    SingleFrame,
)
from isotp_payload_reassembler import IsoTpPayloadReassembler


# ===== 정상 payload 재조립 동작 테스트 =====
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


# ===== 잘못된 프레임 순서나 조립 상태 오염을 예외와 reset으로 처리하는지 테스트 =====
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


# ===== 잘못된 프레임 값이나 지원하지 않는 입력 타입을 명확한 예외로 막는지 테스트 =====
@pytest.mark.parametrize(
    ("frame", "expected_message"),
    [
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
