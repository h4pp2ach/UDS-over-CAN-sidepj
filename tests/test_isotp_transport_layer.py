from dataclasses import dataclass

import can
import pytest

from isotp_errors import (
    IsoTpFrameParseError,
    IsoTpReassemblyError,
    IsoTpSegmentationError,
)
from isotp_frame import FlowStatus
from isotp_transport_layer import (
    IsoTpCanError,
    IsoTpFlowControlError,
    IsoTpPayloadError,
    IsoTpProtocolError,
    IsoTpTimeoutError,
    IsoTpTransportError,
    IsoTpTransportLayer,
    build_flow_control_data,
    decode_st_min_seconds,
)


@dataclass
class IncomingMessage:
    arbitration_id: int
    data: bytes
    is_extended_id: bool = False


class FakeBus:
    def __init__(self, incoming_messages=None):
        self.incoming_messages = list(incoming_messages or [])
        self.sent_messages = []
        self.events = []

    def send(self, msg):
        self.sent_messages.append(msg)
        self.events.append(("send", msg.arbitration_id, bytes(msg.data)))

    def recv(self, timeout=None):
        self.events.append(("recv", timeout))

        if not self.incoming_messages:
            return None

        return self.incoming_messages.pop(0)


class FailingCanBus(FakeBus):
    def __init__(self, *, fail_on: str):
        super().__init__()
        self.fail_on = fail_on

    def send(self, msg):
        if self.fail_on == "send":
            raise can.CanOperationError("send failed")
        return super().send(msg)

    def recv(self, timeout=None):
        if self.fail_on == "recv":
            raise can.CanOperationError("receive failed")
        return super().recv(timeout=timeout)


def incoming(can_id: int, data: str, *, is_extended_id: bool = False) -> IncomingMessage:
    return IncomingMessage(can_id, bytes.fromhex(data), is_extended_id=is_extended_id)


def sent(bus: FakeBus) -> list[tuple[int, bytes]]:
    return [
        (msg.arbitration_id, bytes(msg.data))
        for msg in bus.sent_messages
    ]


def send_events(bus: FakeBus) -> list[tuple[str, int, bytes]]:
    return [
        event
        for event in bus.events
        if event[0] == "send"
    ]


# 기능 그룹: Transport 기본 송수신과 흐름 제어
# 검증 목적: Multi Frame 송수신 시 FC 대기, STmin 간격, Block Size 처리가 올바른 순서로 수행되는지 확인한다.
def test_recv_first_frame_sends_flow_control_and_reassembles_payload():
    bus = FakeBus(
        [
            incoming(0x7E8, "10 0B 62 F1 90 AA BB CC"),
            incoming(0x7E8, "21 DD EE FF 00 11"),
        ]
    )
    transport = IsoTpTransportLayer.for_client(
        bus,
        request_can_id=0x7E0,
        response_can_id=0x7E8,
        block_size=0,
        st_min=0x05,
    )

    payload = transport.recv()

    assert payload == bytes.fromhex("62 F1 90 AA BB CC DD EE FF 00 11")
    assert sent(bus) == [(0x7E0, bytes.fromhex("30 00 05"))]


def test_send_first_frame_waits_for_flow_control_before_consecutive_frame():
    bus = FakeBus([incoming(0x7E8, "30 00 00")])
    transport = IsoTpTransportLayer.for_client(
        bus,
        request_can_id=0x7E0,
        response_can_id=0x7E8,
    )

    transport.send(bytes.fromhex("36 01 AA BB CC DD EE FF 00 11 22"))

    assert [event[0] for event in bus.events] == ["send", "recv", "send"]
    assert send_events(bus) == [
        ("send", 0x7E0, bytes.fromhex("10 0B 36 01 AA BB CC DD")),
        ("send", 0x7E0, bytes.fromhex("21 EE FF 00 11 22")),
    ]


def test_send_applies_st_min_between_consecutive_frames():
    bus = FakeBus([incoming(0x7E8, "30 00 05")])
    sleep_calls = []
    transport = IsoTpTransportLayer.for_client(
        bus,
        request_can_id=0x7E0,
        response_can_id=0x7E8,
        sleep_function=sleep_calls.append,
    )

    transport.send(bytes(range(1, 21)))

    assert sleep_calls == [0.005]
    assert [bytes(msg.data)[0] for msg in bus.sent_messages] == [0x10, 0x21, 0x22]


def test_send_respects_block_size_and_waits_for_next_flow_control():
    bus = FakeBus(
        [
            incoming(0x7E8, "30 02 00"),
            incoming(0x7E8, "30 00 00"),
        ]
    )
    transport = IsoTpTransportLayer.for_client(
        bus,
        request_can_id=0x7E0,
        response_can_id=0x7E8,
    )

    transport.send(bytes(range(1, 29)))

    assert [event[0] for event in bus.events] == [
        "send",
        "recv",
        "send",
        "send",
        "recv",
        "send",
        "send",
    ]
    assert send_events(bus) == [
        ("send", 0x7E0, bytes.fromhex("10 1C 01 02 03 04 05 06")),
        ("send", 0x7E0, bytes.fromhex("21 07 08 09 0A 0B 0C 0D")),
        ("send", 0x7E0, bytes.fromhex("22 0E 0F 10 11 12 13 14")),
        ("send", 0x7E0, bytes.fromhex("23 15 16 17 18 19 1A 1B")),
        ("send", 0x7E0, bytes.fromhex("24 1C")),
    ]


def test_recv_sends_additional_flow_control_after_configured_block_size():
    bus = FakeBus(
        [
            incoming(0x7E8, "10 1C 01 02 03 04 05 06"),
            incoming(0x7E8, "21 07 08 09 0A 0B 0C 0D"),
            incoming(0x7E8, "22 0E 0F 10 11 12 13 14"),
            incoming(0x7E8, "23 15 16 17 18 19 1A 1B"),
            incoming(0x7E8, "24 1C"),
        ]
    )
    transport = IsoTpTransportLayer.for_client(
        bus,
        request_can_id=0x7E0,
        response_can_id=0x7E8,
        block_size=2,
    )

    payload = transport.recv()

    assert payload == bytes(range(1, 29))
    assert sent(bus) == [
        (0x7E0, bytes.fromhex("30 02 00")),
        (0x7E0, bytes.fromhex("30 02 00")),
    ]


# 기능 그룹: Transport 클라이언트와 서버 역할
# 검증 목적: 동일한 request/response CAN ID 설정이 역할에 따라 반대 송수신 방향으로 적용되는지 확인한다.
def test_client_and_server_roles_use_request_and_response_ids_oppositely():
    client_bus = FakeBus()
    client = IsoTpTransportLayer.for_client(
        client_bus,
        request_can_id=0x7E0,
        response_can_id=0x7E8,
    )
    server_bus = FakeBus()
    server = IsoTpTransportLayer.for_server(
        server_bus,
        request_can_id=0x7E0,
        response_can_id=0x7E8,
    )

    client.send(bytes.fromhex("10 03"))
    server.send(bytes.fromhex("50 03"))

    assert sent(client_bus) == [(0x7E0, bytes.fromhex("02 10 03"))]
    assert sent(server_bus) == [(0x7E8, bytes.fromhex("02 50 03"))]


# 기능 그룹: Transport 프레임 길이와 padding
# 검증 목적: SF, FC, 마지막 CF의 padding 및 tx_data_length 기반 분할과 설정값 검증이 정확한지 확인한다.
def test_padding_byte_pads_single_frame_to_tx_data_length():
    bus = FakeBus()
    transport = IsoTpTransportLayer.for_client(
        bus,
        request_can_id=0x7E0,
        response_can_id=0x7E8,
        padding_byte=0xAA,
    )

    transport.send(bytes.fromhex("10 03"))

    assert sent(bus) == [(0x7E0, bytes.fromhex("02 10 03 AA AA AA AA AA"))]


def test_padding_byte_pads_flow_control_frame():
    bus = FakeBus(
        [
            incoming(0x7E8, "10 08 01 02 03 04 05 06"),
            incoming(0x7E8, "21 07 08"),
        ]
    )
    transport = IsoTpTransportLayer.for_client(
        bus,
        request_can_id=0x7E0,
        response_can_id=0x7E8,
        padding_byte=0xCC,
    )

    assert transport.recv() == bytes.fromhex("01 02 03 04 05 06 07 08")
    assert sent(bus) == [(0x7E0, bytes.fromhex("30 00 00 CC CC CC CC CC"))]


def test_padding_byte_pads_last_consecutive_frame():
    bus = FakeBus([incoming(0x7E8, "30 00 00")])
    transport = IsoTpTransportLayer.for_client(
        bus,
        request_can_id=0x7E0,
        response_can_id=0x7E8,
        padding_byte=0x00,
    )

    transport.send(bytes.fromhex("36 01 AA BB CC DD EE FF 00 11 22"))

    assert sent(bus) == [
        (0x7E0, bytes.fromhex("10 0B 36 01 AA BB CC DD")),
        (0x7E0, bytes.fromhex("21 EE FF 00 11 22 00 00")),
    ]


def test_send_uses_tx_data_length_for_segmentation():
    bus = FakeBus([incoming(0x7E8, "30 00 00")])
    transport = IsoTpTransportLayer.for_client(
        bus,
        request_can_id=0x7E0,
        response_can_id=0x7E8,
        tx_data_length=7,
        padding_byte=0x00,
    )

    transport.send(bytes.fromhex("36 01 AA BB CC DD EE FF 00 11 22"))

    assert sent(bus) == [
        (0x7E0, bytes.fromhex("10 0B 36 01 AA BB CC")),
        (0x7E0, bytes.fromhex("21 DD EE FF 00 11 22")),
    ]


def test_invalid_padding_byte_raises_value_error():
    with pytest.raises(ValueError, match="padding_byte must be between 0 and 255"):
        IsoTpTransportLayer.for_client(
            FakeBus(),
            request_can_id=0x7E0,
            response_can_id=0x7E8,
            padding_byte=0x100,
        )


def test_invalid_tx_data_length_raises_value_error():
    with pytest.raises(ValueError, match="tx_data_length must be between 3 and 8"):
        IsoTpTransportLayer.for_client(
            FakeBus(),
            request_can_id=0x7E0,
            response_can_id=0x7E8,
            tx_data_length=2,
        )


# 기능 그룹: 수신 CAN ID 필터링
# 검증 목적: 현재 Transport 대상이 아닌 CAN ID를 건너뛰고 지정된 응답만 처리하는지 확인한다.
def test_recv_ignores_unrelated_can_id_until_response_id_arrives():
    bus = FakeBus(
        [
            incoming(0x123, "02 AA BB"),
            incoming(0x7E8, "02 62 00"),
        ]
    )
    transport = IsoTpTransportLayer.for_client(
        bus,
        request_can_id=0x7E0,
        response_can_id=0x7E8,
    )

    assert transport.recv() == bytes.fromhex("62 00")


def test_recv_keeps_one_deadline_while_skipping_unrelated_can_ids(monkeypatch):
    monotonic_values = iter([100.0, 100.0, 100.25])
    monkeypatch.setattr(
        "isotp_transport_layer.time.monotonic",
        lambda: next(monotonic_values),
    )
    bus = FakeBus(
        [
            incoming(0x123, "02 AA BB"),
            incoming(0x7E8, "02 62 00"),
        ]
    )
    transport = IsoTpTransportLayer.for_client(
        bus,
        request_can_id=0x7E0,
        response_can_id=0x7E8,
    )

    assert transport.recv() == bytes.fromhex("62 00")
    recv_timeouts = [event[1] for event in bus.events if event[0] == "recv"]
    assert recv_timeouts == [pytest.approx(1.0), pytest.approx(0.75)]


# 기능 그룹: CAN 주소 형식과 Transport 설정 검증
# 검증 목적: Standard/Extended CAN ID 구분과 중복 ID, 잘못된 타입의 설정값을 명확한 예외로 거부하는지 확인한다.
def test_extended_can_id_is_explicit_for_send_and_receive():
    bus = FakeBus(
        [
            incoming(0x18DAF110, "02 AA BB", is_extended_id=False),
            incoming(0x18DAF110, "02 62 00", is_extended_id=True),
        ]
    )
    transport = IsoTpTransportLayer.for_client(
        bus,
        request_can_id=0x18DA10F1,
        response_can_id=0x18DAF110,
        is_extended_id=True,
    )

    transport.send(bytes.fromhex("10 03"))

    assert bus.sent_messages[0].is_extended_id is True
    assert transport.recv() == bytes.fromhex("62 00")


def test_standard_address_rejects_29_bit_can_id_without_extended_flag():
    with pytest.raises(ValueError, match="standard tx_can_id"):
        IsoTpTransportLayer.for_client(
            FakeBus(),
            request_can_id=0x18DA10F1,
            response_can_id=0x18DAF110,
        )


def test_same_tx_rx_can_id_is_rejected():
    with pytest.raises(ValueError, match="tx_can_id and rx_can_id must be different"):
        IsoTpTransportLayer.for_client(
            FakeBus(),
            request_can_id=0x700,
            response_can_id=0x700,
        )


def test_can_id_must_be_integer():
    with pytest.raises(TypeError, match="CAN ID must be an integer"):
        IsoTpTransportLayer.for_client(
            FakeBus(),
            request_can_id=1.5,
            response_can_id=0x7E8,
        )


def test_flow_control_byte_must_be_integer():
    with pytest.raises(TypeError, match="block_size must be an integer"):
        IsoTpTransportLayer.for_client(
            FakeBus(),
            request_can_id=0x7E0,
            response_can_id=0x7E8,
            block_size="1",
        )


@pytest.mark.parametrize(
    ("field_name", "field_value", "expected_exception", "expected_message"),
    [
        (
            "frame_timeout_seconds",
            float("nan"),
            ValueError,
            "frame_timeout_seconds must be positive and finite",
        ),
        (
            "frame_timeout_seconds",
            "1",
            TypeError,
            "frame_timeout_seconds must be a number",
        ),
        (
            "max_wait_frame_count",
            1.5,
            TypeError,
            "max_wait_frame_count must be an integer",
        ),
        (
            "max_wait_frame_count",
            -1,
            ValueError,
            "max_wait_frame_count must not be negative",
        ),
    ],
)
def test_transport_rejects_invalid_timeout_and_wait_configuration(
    field_name,
    field_value,
    expected_exception,
    expected_message,
):
    with pytest.raises(expected_exception, match=expected_message):
        IsoTpTransportLayer.for_client(
            FakeBus(),
            request_can_id=0x7E0,
            response_can_id=0x7E8,
            **{field_name: field_value},
        )


# 기능 그룹: Transport payload, 프로토콜 오류와 timeout 처리
# 검증 목적: 송신 payload와 수신 protocol 오류를 원인별 Transport 예외로 변환하고 미수신을 timeout으로 처리하는지 확인한다.
def test_send_wraps_segmentation_error_as_payload_error():
    transport = IsoTpTransportLayer.for_client(
        FakeBus(),
        request_can_id=0x7E0,
        response_can_id=0x7E8,
    )

    with pytest.raises(IsoTpPayloadError, match="ISO-TP payload must not be empty") as exc_info:
        transport.send(b"")

    assert isinstance(exc_info.value.__cause__, IsoTpSegmentationError)


def test_send_does_not_mask_unclassified_value_error(monkeypatch):
    transport = IsoTpTransportLayer.for_client(
        FakeBus(),
        request_can_id=0x7E0,
        response_can_id=0x7E8,
    )

    def raise_unclassified_value_error(*args, **kwargs):
        raise ValueError("unexpected implementation error")

    monkeypatch.setattr(
        "isotp_transport_layer.segment_isotp_payload",
        raise_unclassified_value_error,
    )

    with pytest.raises(ValueError, match="unexpected implementation error") as exc_info:
        transport.send(bytes.fromhex("10 01"))

    assert not isinstance(exc_info.value, IsoTpTransportError)


def test_send_raises_timeout_when_flow_control_does_not_arrive():
    bus = FakeBus()
    transport = IsoTpTransportLayer.for_client(
        bus,
        request_can_id=0x7E0,
        response_can_id=0x7E8,
    )

    with pytest.raises(IsoTpTimeoutError, match="Timed out waiting for ISO-TP CAN frame"):
        transport.send(bytes.fromhex("36 01 AA BB CC DD EE FF 00 11 22"))

    assert sent(bus) == [(0x7E0, bytes.fromhex("10 0B 36 01 AA BB CC DD"))]


def test_recv_raises_timeout_when_expected_frame_does_not_arrive():
    bus = FakeBus([incoming(0x7E8, "10 08 01 02 03 04 05 06")])
    transport = IsoTpTransportLayer.for_client(
        bus,
        request_can_id=0x7E0,
        response_can_id=0x7E8,
    )

    with pytest.raises(IsoTpTimeoutError, match="Timed out waiting for ISO-TP CAN frame"):
        transport.recv()

    assert sent(bus) == [(0x7E0, bytes.fromhex("30 00 00"))]


def test_send_wraps_can_bus_error_as_transport_error():
    transport = IsoTpTransportLayer.for_client(
        FailingCanBus(fail_on="send"),
        request_can_id=0x7E0,
        response_can_id=0x7E8,
    )

    with pytest.raises(IsoTpCanError, match="CAN bus send failed") as exc_info:
        transport.send(bytes.fromhex("10 03"))

    assert isinstance(exc_info.value.__cause__, can.CanOperationError)


def test_recv_wraps_can_bus_error_as_transport_error():
    transport = IsoTpTransportLayer.for_client(
        FailingCanBus(fail_on="recv"),
        request_can_id=0x7E0,
        response_can_id=0x7E8,
    )

    with pytest.raises(IsoTpCanError, match="CAN bus receive failed") as exc_info:
        transport.recv()

    assert isinstance(exc_info.value.__cause__, can.CanOperationError)


def test_send_rejects_non_flow_control_frame_while_waiting_for_fc():
    bus = FakeBus([incoming(0x7E8, "02 62 00")])
    transport = IsoTpTransportLayer.for_client(
        bus,
        request_can_id=0x7E0,
        response_can_id=0x7E8,
    )

    with pytest.raises(IsoTpProtocolError, match="Expected Flow Control Frame"):
        transport.send(bytes.fromhex("36 01 AA BB CC DD EE FF 00 11 22"))


def test_send_wraps_invalid_received_st_min_as_protocol_error():
    bus = FakeBus([incoming(0x7E8, "30 00 80")])
    transport = IsoTpTransportLayer.for_client(
        bus,
        request_can_id=0x7E0,
        response_can_id=0x7E8,
    )

    with pytest.raises(IsoTpProtocolError, match="st_min must be"):
        transport.send(bytes.fromhex("36 01 AA BB CC DD EE FF 00 11 22"))


def test_recv_wraps_malformed_raw_frame_as_protocol_error():
    bus = FakeBus([incoming(0x7E8, "40 00 00")])
    transport = IsoTpTransportLayer.for_client(
        bus,
        request_can_id=0x7E0,
        response_can_id=0x7E8,
    )

    with pytest.raises(IsoTpProtocolError, match="Unsupported ISO-TP frame type") as exc_info:
        transport.recv()

    assert isinstance(exc_info.value.__cause__, IsoTpFrameParseError)


def test_recv_wraps_reassembly_error_as_protocol_error():
    bus = FakeBus(
        [
            incoming(0x7E8, "10 08 01 02 03 04 05 06"),
            incoming(0x7E8, "22 07 08"),
        ]
    )
    transport = IsoTpTransportLayer.for_client(
        bus,
        request_can_id=0x7E0,
        response_can_id=0x7E8,
    )

    with pytest.raises(IsoTpProtocolError, match="Unexpected sequence number") as exc_info:
        transport.recv()

    assert isinstance(exc_info.value.__cause__, IsoTpReassemblyError)


def test_recv_wraps_invalid_can_message_data_as_can_error():
    bus = FakeBus([IncomingMessage(arbitration_id=0x7E8, data=None)])
    transport = IsoTpTransportLayer.for_client(
        bus,
        request_can_id=0x7E0,
        response_can_id=0x7E8,
    )

    with pytest.raises(IsoTpCanError, match="CAN message data is invalid") as exc_info:
        transport.recv()

    assert isinstance(exc_info.value.__cause__, TypeError)


# 기능 그룹: Flow Control 상태와 STmin 처리
# 검증 목적: WAIT 재시도와 한도, OVERFLOW 중단, FC 바이트 생성 및 STmin 디코딩 규칙을 확인한다.
def test_send_allows_wait_flow_control_then_continues_after_cts():
    bus = FakeBus(
        [
            incoming(0x7E8, "31 00 00"),
            incoming(0x7E8, "30 00 00"),
        ]
    )
    transport = IsoTpTransportLayer.for_client(
        bus,
        request_can_id=0x7E0,
        response_can_id=0x7E8,
    )

    transport.send(bytes.fromhex("36 01 AA BB CC DD EE FF 00 11 22"))

    assert [event[0] for event in bus.events] == ["send", "recv", "recv", "send"]


def test_send_raises_when_wait_flow_control_limit_is_exceeded():
    bus = FakeBus(
        [
            incoming(0x7E8, "31 00 00"),
            incoming(0x7E8, "31 00 00"),
        ]
    )
    transport = IsoTpTransportLayer.for_client(
        bus,
        request_can_id=0x7E0,
        response_can_id=0x7E8,
        max_wait_frame_count=1,
    )

    with pytest.raises(IsoTpFlowControlError, match="WAIT frame limit exceeded"):
        transport.send(bytes.fromhex("36 01 AA BB CC DD EE FF 00 11 22"))


def test_flow_control_overflow_stops_sender():
    bus = FakeBus([incoming(0x7E8, "32 00 00")])
    transport = IsoTpTransportLayer.for_client(
        bus,
        request_can_id=0x7E0,
        response_can_id=0x7E8,
    )

    with pytest.raises(IsoTpFlowControlError, match="overflow"):
        transport.send(bytes.fromhex("36 01 AA BB CC DD EE FF 00 11 22"))


def test_build_flow_control_data_and_decode_st_min():
    assert build_flow_control_data(
        FlowStatus.CONTINUE_TO_SEND,
        block_size=8,
        st_min=0x0A,
    ) == bytes.fromhex("30 08 0A")
    assert decode_st_min_seconds(0x7F) == 0.127
    assert decode_st_min_seconds(0xF1) == 0.0001


def test_invalid_st_min_raises_value_error():
    with pytest.raises(ValueError, match="st_min must be 0x00-0x7F or 0xF1-0xF9"):
        decode_st_min_seconds(0x80)
