import pytest

from send_isotp_scenario import build_isotp_messages, find_scenario
from isotp_tx_segmenter import segment_isotp_payload


# 기능 그룹: ISO-TP 송신 payload 분할
# 검증 목적: payload 크기와 tx_data_length에 따라 SF 또는 FF/CF 바이트열로 정확히 분할되는지 확인한다.
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


# 기능 그룹: ISO-TP 최대 길이와 Sequence Number 경계
# 검증 목적: 4095-byte payload를 허용하고 CF Sequence Number가 0xF 다음 0x0으로 순환하는지 확인한다.
def test_segmenter_supports_max_payload_and_wraps_sequence_number():
    payload = bytes(index % 256 for index in range(0xFFF))

    frames = segment_isotp_payload(payload)

    assert len(frames) == 586
    assert frames[0][:2] == bytes.fromhex("1F FF")
    assert frames[15][0] == 0x2F
    assert frames[16][0] == 0x20


# 기능 그룹: 송신 시나리오와 CAN 메시지 생성
# 검증 목적: 분할 결과에 CAN ID가 적용되고 예제 시나리오가 기대한 프레임 수와 Sequence Number를 만드는지 확인한다.
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


# 기능 그룹: 송신 분할기와 시나리오 입력 검증
# 검증 목적: 빈 payload, 최대 길이 초과, 잘못된 시나리오 키와 tx_data_length를 명확한 예외로 거부하는지 확인한다.
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


def test_non_integer_tx_data_length_raises_type_error():
    with pytest.raises(TypeError, match="tx_data_length must be an integer"):
        segment_isotp_payload(bytes.fromhex("10 01"), tx_data_length=8.0)
