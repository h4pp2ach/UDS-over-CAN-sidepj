from dataclasses import dataclass
from enum import Enum


class IsoTpFrameType(Enum):
    SINGLE_FRAME = "SF"
    FIRST_FRAME = "FF"
    CONSECUTIVE_FRAME = "CF"
    FLOW_CONTROL = "FC"


class FlowStatus(Enum):
    CONTINUE_TO_SEND = 0
    WAIT = 1
    OVERFLOW = 2


@dataclass
class SingleFrame:
    length: int
    payload: bytes


@dataclass
class FirstFrame:
    total_length: int
    payload: bytes


@dataclass
class ConsecutiveFrame:
    sequence_number: int
    payload: bytes


@dataclass
class FlowControlFrame:
    flow_status: FlowStatus
    block_size: int
    st_min: int


def parse_isotp_frame(data: bytes):
    if len(data) == 0:
        raise ValueError("Empty CAN data")

    # ISO-TP는 첫 PCI 바이트의 상위 4비트로 프레임 타입을 구분함.
    # 하위 4비트는 프레임 타입마다 길이, 순번, 흐름 상태 등으로 다르게 해석. (ISO 15765-2 참고)
    pci = data[0]
    frame_type = (pci & 0xF0) >> 4

    if frame_type == 0x0:
        length = pci & 0x0F
        payload = data[1:1 + length]

        if len(payload) != length:
            raise ValueError("Single Frame payload length mismatch")

        return SingleFrame(length=length, payload=payload)

    if frame_type == 0x1:
        if len(data) < 2:
            raise ValueError("First Frame requires at least 2 bytes")

        total_length = ((pci & 0x0F) << 8) | data[1]
        payload = data[2:]

        return FirstFrame(total_length=total_length, payload=payload)

    if frame_type == 0x2:
        sequence_number = pci & 0x0F
        payload = data[1:]

        return ConsecutiveFrame(
            sequence_number=sequence_number,
            payload=payload,
        )

    if frame_type == 0x3:
        if len(data) < 3:
            raise ValueError("Flow Control Frame requires at least 3 bytes")

        # Flow Control 프레임의 하위 4비트: 수신 측의 흐름 제어 상태
        fs_raw = pci & 0x0F

        try:
            flow_status = FlowStatus(fs_raw)
        except ValueError:
            raise ValueError(f"Invalid Flow Status: {fs_raw}")

        return FlowControlFrame(
            flow_status=flow_status,
            block_size=data[1],
            st_min=data[2],
        )

    raise ValueError(f"Unsupported ISO-TP frame type: {frame_type}")
