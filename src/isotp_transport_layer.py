from dataclasses import dataclass
import math
import time
from typing import Callable

import can

from isotp_errors import (
    IsoTpCanError,
    IsoTpFlowControlError,
    IsoTpFrameParseError,
    IsoTpPayloadError,
    IsoTpProtocolError,
    IsoTpReassemblyError,
    IsoTpSegmentationError,
    IsoTpTimeoutError,
    IsoTpTransportError,
)
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
        sleep_function: Callable[[float], None] = time.sleep,
    ) -> None:
        validate_address(address)
        validate_flow_control_byte(block_size, "block_size")
        validate_st_min(st_min)
        validate_tx_data_length(tx_data_length)
        validate_padding_byte(padding_byte)

        validate_positive_seconds(frame_timeout_seconds, "frame_timeout_seconds")
        validate_non_negative_integer(max_wait_frame_count, "max_wait_frame_count")

        self._bus = bus
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
        except IsoTpSegmentationError as exc:
            raise IsoTpPayloadError(str(exc)) from exc

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

        validate_positive_seconds(frame_timeout_seconds, "timeout_seconds")

        reassembler = IsoTpPayloadReassembler()
        received_in_block = 0

        while True:
            raw_frame = self._recv_isotp_frame(frame_timeout_seconds)

            try:
                payload = reassembler.feed(raw_frame)
            except IsoTpReassemblyError as exc:
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
                try:
                    validate_st_min(frame.st_min)
                except ValueError as exc:
                    raise IsoTpProtocolError(str(exc)) from exc
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
            data = bytes(msg.data)
        except (AttributeError, TypeError, ValueError) as exc:
            raise IsoTpCanError("CAN message data is invalid") from exc

        try:
            return parse_isotp_frame(data)
        except IsoTpFrameParseError as exc:
            raise IsoTpProtocolError(str(exc)) from exc

    def _recv_message_for_rx_id(self, timeout_seconds: float):
        deadline = time.monotonic() + timeout_seconds

        while True:
            remaining_seconds = deadline - time.monotonic()

            if remaining_seconds <= 0:
                raise IsoTpTimeoutError("Timed out waiting for ISO-TP CAN frame")

            try:
                msg = self._bus.recv(timeout=remaining_seconds)
            except can.CanError as exc:
                raise IsoTpCanError("CAN bus receive failed") from exc

            if msg is None:
                raise IsoTpTimeoutError("Timed out waiting for ISO-TP CAN frame")

            if msg.arbitration_id != self.address.rx_can_id:
                continue

            if (
                getattr(msg, "is_extended_id", False)
                != self.address.is_extended_id
            ):
                continue

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

        try:
            self._bus.send(msg)
        except can.CanError as exc:
            raise IsoTpCanError("CAN bus send failed") from exc

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


def validate_address(address: IsoTpAddress) -> None:
    validate_can_id(address.tx_can_id)
    validate_can_id(address.rx_can_id)

    if type(address.is_extended_id) is not bool:
        raise TypeError("is_extended_id must be a boolean")

    if not address.is_extended_id:
        if address.tx_can_id > MAX_STANDARD_CAN_ID:
            raise ValueError("standard tx_can_id must be between 0x000 and 0x7FF")

        if address.rx_can_id > MAX_STANDARD_CAN_ID:
            raise ValueError("standard rx_can_id must be between 0x000 and 0x7FF")

    if address.tx_can_id == address.rx_can_id:
        raise ValueError("tx_can_id and rx_can_id must be different")


def validate_flow_control_byte(value: int, name: str) -> None:
    if type(value) is not int:
        raise TypeError(f"{name} must be an integer")

    if value < 0 or value > MAX_FLOW_CONTROL_BYTE:
        raise ValueError(f"{name} must be between 0 and 255")


def validate_positive_seconds(value: float, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number")

    try:
        is_finite = math.isfinite(value)
    except OverflowError:
        is_finite = False

    if value <= 0 or not is_finite:
        raise ValueError(f"{name} must be positive and finite")


def validate_non_negative_integer(value: int, name: str) -> None:
    if type(value) is not int:
        raise TypeError(f"{name} must be an integer")

    if value < 0:
        raise ValueError(f"{name} must not be negative")


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
