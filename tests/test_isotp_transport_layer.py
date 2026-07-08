from dataclasses import dataclass

import pytest

from isotp_frame import FlowStatus
from isotp_transport_layer import (
    CanMessageRouter,
    IsoTpFlowControlError,
    IsoTpProtocolError,
    IsoTpTimeoutError,
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


class NonWeakrefBus:
    __slots__ = ("incoming_messages", "sent_messages", "events")

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


def test_transports_on_same_bus_do_not_drop_each_other_messages():
    bus = FakeBus(
        [
            incoming(0x709, "02 BB 00"),
            incoming(0x708, "02 AA 00"),
        ]
    )
    transport_a = IsoTpTransportLayer.for_client(
        bus,
        request_can_id=0x700,
        response_can_id=0x708,
    )
    transport_b = IsoTpTransportLayer.for_client(
        bus,
        request_can_id=0x701,
        response_can_id=0x709,
    )

    assert transport_a.recv() == bytes.fromhex("AA 00")
    assert transport_b.recv() == bytes.fromhex("BB 00")


def test_non_weakref_bus_requires_explicit_router():
    with pytest.raises(TypeError, match="pass CanMessageRouter explicitly"):
        IsoTpTransportLayer.for_client(
            NonWeakrefBus(),
            request_can_id=0x700,
            response_can_id=0x708,
        )


def test_explicit_router_supports_non_weakref_bus():
    bus = NonWeakrefBus(
        [
            incoming(0x709, "02 BB 00"),
            incoming(0x708, "02 AA 00"),
        ]
    )
    router = CanMessageRouter(bus)
    transport_a = IsoTpTransportLayer.for_client(
        router,
        request_can_id=0x700,
        response_can_id=0x708,
    )
    transport_b = IsoTpTransportLayer.for_client(
        router,
        request_can_id=0x701,
        response_can_id=0x709,
    )

    assert transport_a.recv() == bytes.fromhex("AA 00")
    assert transport_b.recv() == bytes.fromhex("BB 00")


def test_router_bounds_pending_messages_per_can_id():
    bus = FakeBus(
        [
            incoming(0x709, "02 BB 01"),
            incoming(0x709, "02 BB 02"),
            incoming(0x708, "02 AA 00"),
        ]
    )
    router = CanMessageRouter(bus, max_pending_per_id=1)
    transport_a = IsoTpTransportLayer.for_client(
        router,
        request_can_id=0x700,
        response_can_id=0x708,
    )
    transport_b = IsoTpTransportLayer.for_client(
        router,
        request_can_id=0x701,
        response_can_id=0x709,
    )

    assert transport_a.recv() == bytes.fromhex("AA 00")
    assert transport_b.recv() == bytes.fromhex("BB 02")


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


def test_same_tx_rx_can_id_is_rejected_by_default():
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


def test_send_wraps_segmenter_value_error():
    transport = IsoTpTransportLayer.for_client(
        FakeBus(),
        request_can_id=0x7E0,
        response_can_id=0x7E8,
    )

    with pytest.raises(IsoTpProtocolError, match="ISO-TP payload must not be empty"):
        transport.send(b"")


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
