from isotp_frame import (
    ConsecutiveFrame,
    FirstFrame,
    FlowControlFrame,
    SingleFrame,
)


class IsoTpPayloadReassembler:
    def __init__(self) -> None:
        self.reset()

    @property
    def is_in_progress(self) -> bool:
        return self._total_length is not None

    def reset(self) -> None:
        self._total_length = None
        self._payload = bytearray()
        self._expected_sequence_number = 1

    def feed(self, frame: object) -> bytes | None:
        if isinstance(frame, SingleFrame):
            return self._handle_single_frame(frame)

        if isinstance(frame, FirstFrame):
            return self._handle_first_frame(frame)

        if isinstance(frame, ConsecutiveFrame):
            return self._handle_consecutive_frame(frame)

        if isinstance(frame, FlowControlFrame):
            raise ValueError("Flow Control Frame cannot be reassembled as payload")

        raise TypeError(f"Unsupported ISO-TP frame object: {type(frame).__name__}")

    def _handle_single_frame(self, frame: SingleFrame) -> bytes:
        if self.is_in_progress:
            self.reset()
            raise ValueError("Single Frame received while multi-frame payload is in progress")

        self.reset()

        if frame.length <= 0:
            raise ValueError("Single Frame length must be positive")

        if frame.length != len(frame.payload):
            raise ValueError("Single Frame length does not match payload length")

        return frame.payload

    def _handle_first_frame(self, frame: FirstFrame) -> None:
        if self.is_in_progress:
            self.reset()
            raise ValueError("First Frame received while multi-frame payload is in progress")

        self.reset()

        if frame.total_length <= 0:
            raise ValueError("First Frame total length must be positive")

        if len(frame.payload) == 0:
            raise ValueError("First Frame payload must not be empty")

        if len(frame.payload) >= frame.total_length:
            raise ValueError("First Frame payload must be shorter than total length")

        self._total_length = frame.total_length
        self._payload = bytearray(frame.payload)
        self._expected_sequence_number = 1

        return None

    def _handle_consecutive_frame(self, frame: ConsecutiveFrame) -> bytes | None:
        if not self.is_in_progress:
            raise ValueError("Consecutive Frame received before First Frame")

        if len(frame.payload) == 0:
            self.reset()
            raise ValueError("Consecutive Frame payload must not be empty")

        if frame.sequence_number != self._expected_sequence_number:
            expected = self._expected_sequence_number
            self.reset()
            raise ValueError(
                f"Unexpected sequence number: expected {expected}, got {frame.sequence_number}"
            )

        self._payload.extend(frame.payload)
        self._expected_sequence_number = (self._expected_sequence_number + 1) % 16

        # 마지막 Consecutive Frame은 padding을 포함할 수 있으므로 필요한 길이까지만 반환.
        if len(self._payload) >= self._total_length:
            payload = bytes(self._payload[:self._total_length])
            self.reset()
            return payload

        return None
