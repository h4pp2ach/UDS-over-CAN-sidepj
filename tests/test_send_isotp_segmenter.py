import pytest

from send_isotp_scenario import build_isotp_messages, find_scenario
from isotp_tx_segmenter import segment_isotp_payload


# ISO-TP TX segmenter가 payload를 SF/FF/CF data bytes로 올바르게 분할하는지 테스트.
def test_build_single_frame_message():
    frames = segment_isotp_payload(bytes.fromhex("10 01"))

    assert frames == [bytes.fromhex("02 10 01")]


def test_build_multi_frame_messages():
    frames = segment_isotp_payload(bytes.fromhex("36 01 AA BB CC DD EE FF 00 11 22"))

    assert frames == [
        bytes.fromhex("10 0B 36 01 AA BB CC DD"),
        bytes.fromhex("21 EE FF 00 11 22"),
    ]


def test_segmenter_respects_tx_data_length():
    frames = segment_isotp_payload(
        bytes.fromhex("36 01 AA BB CC DD EE FF 00 11 22"),
        tx_data_length=7,
    )

    assert frames == [
        bytes.fromhex("10 0B 36 01 AA BB CC"),
        bytes.fromhex("21 DD EE FF 00 11 22"),
    ]
    assert all(len(frame) <= 7 for frame in frames)


def test_build_isotp_messages_wraps_segmented_data_with_can_id():
    messages = build_isotp_messages(0x7E0, bytes.fromhex("10 01"))

    assert len(messages) == 1
    assert messages[0].arbitration_id == 0x7E0
    assert bytes(messages[0].data) == bytes.fromhex("02 10 01")


@pytest.mark.parametrize(
    ("scenario_key", "expected_frame_count", "expected_last_sn"),
    [
        ("2", 2, 1),
        ("3", 3, 2),
        ("4", 5, 4),
    ],
)
def test_multi_frame_scenarios_have_varied_sequence_numbers(
    scenario_key,
    expected_frame_count,
    expected_last_sn,
):
    scenario = find_scenario(scenario_key)

    frames = segment_isotp_payload(scenario.payload)

    assert len(frames) == expected_frame_count
    assert frames[-1][0] == 0x20 | expected_last_sn


def test_find_scenario_by_menu_key():
    scenario = find_scenario("4")

    assert scenario.name == "Multi Frame - long TransferData payload (FF + CF 4)"
    assert scenario.can_id == 0x200


@pytest.mark.parametrize(
    ("payload", "expected_message"),
    [
        (b"", "ISO-TP payload must not be empty"),
        (bytes([0xAA]) * 0x1000, "ISO-TP payload supports up to 4095 bytes"),
    ],
)
def test_invalid_payload_raises_value_error(payload, expected_message):
    with pytest.raises(ValueError, match=expected_message):
        segment_isotp_payload(payload)


def test_unknown_scenario_raises_value_error():
    with pytest.raises(ValueError, match="Unknown scenario: 9"):
        find_scenario("9")


def test_invalid_tx_data_length_raises_value_error():
    with pytest.raises(ValueError, match="tx_data_length must be between 3 and 8"):
        segment_isotp_payload(bytes.fromhex("10 01"), tx_data_length=2)
