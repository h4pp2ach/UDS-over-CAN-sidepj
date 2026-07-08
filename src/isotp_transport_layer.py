from collections import deque
from dataclasses import dataclass
import time
from typing import Callable
import weakref
from weakref import WeakKeyDictionary

import can

from isotp_frame import (
    ConsecutiveFrame,
    FirstFrame,
    FlowControlFrame,
    FlowStatus,
    SingleFrame,
    parse_isotp_frame,
)
from isotp_payload_reassembler import IsoTpPayloadReassembler
from isotp_tx_segmenter import (
    MAX_CLASSIC_CAN_DATA_LENGTH,
    segment_isotp_payload,
    validate_tx_data_length,
)

MAX_CAN_ID = 0x1FFFFFFF
MAX_STANDARD_CAN_ID = 0x7FF
MAX_FLOW_CONTROL_BYTE = 0xFF
DEFAULT_FRAME_TIMEOUT_SECONDS = 1.0
DEFAULT_MAX_WAIT_FRAME_COUNT = 8
DEFAULT_MAX_PENDING_MESSAGES_PER_ID = 100


class IsoTpTransportError(Exception):
    pass


class IsoTpTimeoutError(IsoTpTransportError):
    pass


class IsoTpProtocolError(IsoTpTransportError):
    pass


class IsoTpFlowControlError(IsoTpTransportError):
    pass


@dataclass(frozen=True)
class CanMessageKey:
    can_id: int
    is_extended_id: bool


class CanMessageRouter:
    def __init__(
        self,
        bus,
        *,
        max_pending_per_id: int = DEFAULT_MAX_PENDING_MESSAGES_PER_ID,
        monotonic_function: Callable[[], float] = time.monotonic,
    ) -> None:
        validate_max_pending_per_id(max_pending_per_id)
        self._bus = bus
        self.max_pending_per_id = max_pending_per_id
        self._monotonic = monotonic_function
        self._pending_messages: dict[CanMessageKey, deque] = {}

    def send(self, msg) -> None:
        self._bus.send(msg)

    def recv(
        self,
        *,
        can_id: int,
        is_extended_id: bool,
        timeout_seconds: float,
    ):
        key = CanMessageKey(can_id=can_id, is_extended_id=is_extended_id)
        queued_msg = self._pop_pending_message(key)

        if queued_msg is not None:
            return queued_msg

        deadline = self._monotonic() + timeout_seconds

        while True:
            remaining_seconds = deadline - self._monotonic()

            if remaining_seconds <= 0:
                return None

            msg = self._bus.recv(timeout=remaining_seconds)

            if msg is None:
                return None

            msg_key = self._message_key(msg)

            if msg_key == key:
                return msg

            self._queue_pending_message(msg_key, msg)

    def _pop_pending_message(self, key: CanMessageKey):
        pending = self._pending_messages.get(key)

        if not pending:
            return None

        msg = pending.popleft()

        if not pending:
            self._pending_messages.pop(key, None)

        return msg

    def _message_key(self, msg) -> CanMessageKey:
        return CanMessageKey(
            can_id=msg.arbitration_id,
            is_extended_id=getattr(msg, "is_extended_id", False),
        )

    def _queue_pending_message(self, key: CanMessageKey, msg) -> None:
        if self.max_pending_per_id == 0:
            return

        pending = self._pending_messages.setdefault(key, deque())

        if len(pending) >= self.max_pending_per_id:
            pending.popleft()

        pending.append(msg)


_MESSAGE_ROUTERS_BY_BUS = WeakKeyDictionary()


def get_message_router(bus) -> CanMessageRouter:
    if isinstance(bus, CanMessageRouter):
        return bus

    try:
        weakref.ref(bus)
    except TypeError as exc:
        raise TypeError(
            "bus must support weak references or pass CanMessageRouter explicitly"
        ) from exc

    try:
        router = _MESSAGE_ROUTERS_BY_BUS.get(bus)
    except TypeError as exc:
        raise TypeError(
            "bus must support weak references or pass CanMessageRouter explicitly"
        ) from exc

    if router is None:
        router = CanMessageRouter(bus)
        _MESSAGE_ROUTERS_BY_BUS[bus] = router

    return router


@dataclass(frozen=True)
class IsoTpAddress:
    tx_can_id: int
    rx_can_id: int
    is_extended_id: bool = False

    @classmethod
    def for_client(
        cls,
        request_can_id: int,
        response_can_id: int,
        *,
        is_extended_id: bool = False,
    ) -> "IsoTpAddress":
        return cls(
            tx_can_id=request_can_id,
            rx_can_id=response_can_id,
            is_extended_id=is_extended_id,
        )

    @classmethod
    def for_server(
        cls,
        request_can_id: int,
        response_can_id: int,
        *,
        is_extended_id: bool = False,
    ) -> "IsoTpAddress":
        return cls(
            tx_can_id=response_can_id,
            rx_can_id=request_can_id,
            is_extended_id=is_extended_id,
        )


class IsoTpTransportLayer:
    def __init__(
        self,
        bus,
        address: IsoTpAddress,
        *,
        block_size: int = 0,
        st_min: int = 0,
        frame_timeout_seconds: float = DEFAULT_FRAME_TIMEOUT_SECONDS,
        max_wait_frame_count: int = DEFAULT_MAX_WAIT_FRAME_COUNT,
        tx_data_length: int = MAX_CLASSIC_CAN_DATA_LENGTH,
        padding_byte: int | None = None,
        allow_same_can_id: bool = False,
        sleep_function: Callable[[float], None] = time.sleep,
    ) -> None:
        validate_address(address, allow_same_can_id=allow_same_can_id)
        validate_flow_control_byte(block_size, "block_size")
        validate_st_min(st_min)
        validate_tx_data_length(tx_data_length)
        validate_padding_byte(padding_byte)

        if frame_timeout_seconds <= 0:
            raise ValueError("frame_timeout_seconds must be positive")

        if max_wait_frame_count < 0:
            raise ValueError("max_wait_frame_count must not be negative")

        self._message_router = get_message_router(bus)
        self.address = address
        self.block_size = block_size
        self.st_min = st_min
        self.frame_timeout_seconds = frame_timeout_seconds
        self.max_wait_frame_count = max_wait_frame_count
        self.tx_data_length = tx_data_length
        self.padding_byte = padding_byte
        self._sleep = sleep_function

    @classmethod
    def for_client(
        cls,
        bus,
        *,
        request_can_id: int,
        response_can_id: int,
        is_extended_id: bool = False,
        **kwargs,
    ) -> "IsoTpTransportLayer":
        return cls(
            bus,
            IsoTpAddress.for_client(
                request_can_id,
                response_can_id,
                is_extended_id=is_extended_id,
            ),
            **kwargs,
        )

    @classmethod
    def for_server(
        cls,
        bus,
        *,
        request_can_id: int,
        response_can_id: int,
        is_extended_id: bool = False,
        **kwargs,
    ) -> "IsoTpTransportLayer":
        return cls(
            bus,
            IsoTpAddress.for_server(
                request_can_id,
                response_can_id,
                is_extended_id=is_extended_id,
            ),
            **kwargs,
        )

    def send(self, payload: bytes) -> None:
        try:
            frames = segment_isotp_payload(
                payload,
                tx_data_length=self.tx_data_length,
            )
        except ValueError as exc:
            raise IsoTpProtocolError(str(exc)) from exc

        if len(frames) == 1:
            self._send_can_data(frames[0])
            return

        self._send_can_data(frames[0])
        flow_control = self._wait_for_flow_control()
        block_size = flow_control.block_size
        st_min_seconds = decode_st_min_seconds(flow_control.st_min)
        sent_in_block = 0
        sent_any_consecutive_frame = False

        for frame_index, frame_data in enumerate(frames[1:], start=1):
            if sent_any_consecutive_frame and st_min_seconds > 0:
                self._sleep(st_min_seconds)

            self._send_can_data(frame_data)
            sent_any_consecutive_frame = True

            if block_size == 0:
                continue

            sent_in_block += 1

            if sent_in_block < block_size:
                continue

            if frame_index == len(frames) - 1:
                continue

            flow_control = self._wait_for_flow_control()
            block_size = flow_control.block_size
            st_min_seconds = decode_st_min_seconds(flow_control.st_min)
            sent_in_block = 0

    def recv(self, timeout_seconds: float | None = None) -> bytes:
        frame_timeout_seconds = (
            self.frame_timeout_seconds if timeout_seconds is None else timeout_seconds
        )

        if frame_timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")

        reassembler = IsoTpPayloadReassembler()
        received_in_block = 0

        while True:
            raw_frame = self._recv_isotp_frame(frame_timeout_seconds)

            try:
                payload = reassembler.feed(raw_frame)
            except ValueError as exc:
                raise IsoTpProtocolError(str(exc)) from exc

            if isinstance(raw_frame, SingleFrame):
                if payload is None:
                    raise IsoTpProtocolError("Single Frame did not produce a payload")
                return payload

            if isinstance(raw_frame, FirstFrame):
                self._send_flow_control()
                received_in_block = 0
                continue

            if isinstance(raw_frame, ConsecutiveFrame):
                if payload is not None:
                    return payload

                received_in_block += 1

                if self.block_size > 0 and received_in_block >= self.block_size:
                    self._send_flow_control()
                    received_in_block = 0

                continue

            raise IsoTpProtocolError(
                f"Unexpected ISO-TP frame while receiving payload: {type(raw_frame).__name__}"
            )

    def _wait_for_flow_control(self) -> FlowControlFrame:
        wait_frame_count = 0

        while True:
            frame = self._recv_isotp_frame(self.frame_timeout_seconds)

            if not isinstance(frame, FlowControlFrame):
                raise IsoTpProtocolError(
                    f"Expected Flow Control Frame, got {type(frame).__name__}"
                )

            if frame.flow_status == FlowStatus.CONTINUE_TO_SEND:
                validate_st_min(frame.st_min)
                return frame

            if frame.flow_status == FlowStatus.WAIT:
                wait_frame_count += 1

                if wait_frame_count > self.max_wait_frame_count:
                    raise IsoTpFlowControlError("Flow Control WAIT frame limit exceeded")

                continue

            if frame.flow_status == FlowStatus.OVERFLOW:
                raise IsoTpFlowControlError("Receiver reported Flow Control overflow")

            raise IsoTpFlowControlError(
                f"Unsupported Flow Control status: {frame.flow_status}"
            )

    def _recv_isotp_frame(self, timeout_seconds: float):
        msg = self._recv_message_for_rx_id(timeout_seconds)

        try:
            return parse_isotp_frame(bytes(msg.data))
        except ValueError as exc:
            raise IsoTpProtocolError(str(exc)) from exc

    def _recv_message_for_rx_id(self, timeout_seconds: float):
        msg = self._message_router.recv(
            can_id=self.address.rx_can_id,
            is_extended_id=self.address.is_extended_id,
            timeout_seconds=timeout_seconds,
        )

        if msg is None:
            raise IsoTpTimeoutError("Timed out waiting for ISO-TP CAN frame")

        return msg

    def _send_flow_control(self) -> None:
        self._send_can_data(
            build_flow_control_data(
                FlowStatus.CONTINUE_TO_SEND,
                block_size=self.block_size,
                st_min=self.st_min,
            )
        )

    def _send_can_data(self, data: bytes) -> None:
        data = self._pad_can_data(data)
        msg = can.Message(
            arbitration_id=self.address.tx_can_id,
            data=data,
            is_extended_id=self.address.is_extended_id,
            check=True,
        )
        self._message_router.send(msg)

    def _pad_can_data(self, data: bytes) -> bytes:
        if len(data) > self.tx_data_length:
            raise IsoTpProtocolError("CAN data exceeds tx_data_length")

        if self.padding_byte is None:
            return data

        return data + bytes([self.padding_byte]) * (self.tx_data_length - len(data))


def build_flow_control_data(
    flow_status: FlowStatus,
    *,
    block_size: int,
    st_min: int,
) -> bytes:
    validate_flow_control_byte(block_size, "block_size")
    validate_st_min(st_min)

    return bytes([0x30 | flow_status.value, block_size, st_min])


def decode_st_min_seconds(st_min: int) -> float:
    validate_st_min(st_min)

    if st_min <= 0x7F:
        return st_min / 1000

    return (st_min - 0xF0) / 10000


def validate_can_id(can_id: int) -> None:
    if type(can_id) is not int:
        raise TypeError("CAN ID must be an integer")

    if can_id < 0 or can_id > MAX_CAN_ID:
        raise ValueError("CAN ID must be between 0x00000000 and 0x1FFFFFFF")


def validate_address(address: IsoTpAddress, *, allow_same_can_id: bool) -> None:
    validate_can_id(address.tx_can_id)
    validate_can_id(address.rx_can_id)

    if type(address.is_extended_id) is not bool:
        raise TypeError("is_extended_id must be a boolean")

    if not address.is_extended_id:
        if address.tx_can_id > MAX_STANDARD_CAN_ID:
            raise ValueError("standard tx_can_id must be between 0x000 and 0x7FF")

        if address.rx_can_id > MAX_STANDARD_CAN_ID:
            raise ValueError("standard rx_can_id must be between 0x000 and 0x7FF")

    if not allow_same_can_id and address.tx_can_id == address.rx_can_id:
        raise ValueError("tx_can_id and rx_can_id must be different")


def validate_flow_control_byte(value: int, name: str) -> None:
    if type(value) is not int:
        raise TypeError(f"{name} must be an integer")

    if value < 0 or value > MAX_FLOW_CONTROL_BYTE:
        raise ValueError(f"{name} must be between 0 and 255")


def validate_max_pending_per_id(max_pending_per_id: int) -> None:
    if type(max_pending_per_id) is not int:
        raise TypeError("max_pending_per_id must be an integer")

    if max_pending_per_id < 0:
        raise ValueError("max_pending_per_id must not be negative")


def validate_padding_byte(padding_byte: int | None) -> None:
    if padding_byte is None:
        return

    if type(padding_byte) is not int:
        raise TypeError("padding_byte must be an integer")

    if padding_byte < 0 or padding_byte > 0xFF:
        raise ValueError("padding_byte must be between 0 and 255")


def validate_st_min(st_min: int) -> None:
    validate_flow_control_byte(st_min, "st_min")

    if st_min <= 0x7F or 0xF1 <= st_min <= 0xF9:
        return

    raise ValueError("st_min must be 0x00-0x7F or 0xF1-0xF9")
