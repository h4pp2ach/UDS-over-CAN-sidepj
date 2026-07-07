from pathlib import Path
import sys

import can

PROJECT_ROOT = Path(__file__).resolve().parent
SRC_DIR = PROJECT_ROOT / "src"
CHANNEL = "vcan0"
DONE_SEPARATOR = "-" * 96
FIRST_FRAME_PAYLOAD_LENGTH = 6
CONSECUTIVE_FRAME_PAYLOAD_LENGTH = 7

sys.path.insert(0, str(SRC_DIR))
from python_can_parser import parse_python_can_message
from isotp_frame import (
    ConsecutiveFrame,
    FirstFrame,
    FlowControlFrame,
    SingleFrame,
    parse_isotp_frame,
)
from isotp_payload_reassembler import IsoTpPayloadReassembler


def format_data(data: bytes | None) -> str:
    if data is None:
        return ""

    return data.hex(" ").upper()


def format_frame_id(frame) -> str:
    return f"ch = {frame.channel}  id = 0x{frame.can_id:08X}  dlc = {frame.dlc}"


def print_header():
    print(f"Listening ISO-TP on {CHANNEL}")
    print("Press Ctrl+C to stop.")
    print()


def calculate_total_frame_count(total_payload_length: int) -> int:
    remaining_length = total_payload_length - FIRST_FRAME_PAYLOAD_LENGTH

    if remaining_length <= 0:
        return 1

    consecutive_frame_count = (
        remaining_length + CONSECUTIVE_FRAME_PAYLOAD_LENGTH - 1
    ) // CONSECUTIVE_FRAME_PAYLOAD_LENGTH

    return 1 + consecutive_frame_count


def log_isotp_frame(
    frame,
    reassemblers: dict[tuple[str, int], IsoTpPayloadReassembler],
    sequence_states: dict[tuple[str, int], dict[str, int]],
):
    print(f"CAN   {format_frame_id(frame)}  data = {format_data(frame.data)}")

    try:
        isotp_frame = parse_isotp_frame(frame.data)
    except ValueError as exc:
        print(f"ERROR reason = {exc}")
        return

    if isinstance(isotp_frame, FlowControlFrame):
        print(
            "ISO-TP   "
            "[1/1]  "
            "type = FC  "
            f"status = {isotp_frame.flow_status.name}  "
            f"block_size = {isotp_frame.block_size}  "
            f"st_min = {isotp_frame.st_min}"
        )
        return

    key = (frame.channel, frame.can_id)
    reassembler = reassemblers.setdefault(key, IsoTpPayloadReassembler())

    try:
        if isinstance(isotp_frame, FirstFrame):
            total_frame_count = calculate_total_frame_count(isotp_frame.total_length)
            sequence_states[key] = {
                "current": 1,
                "total": total_frame_count,
            }
        elif isinstance(isotp_frame, ConsecutiveFrame):
            state = sequence_states.get(key)
            if state is not None:
                state["current"] += 1

        payload = reassembler.feed(isotp_frame)
    except ValueError as exc:
        sequence_states.pop(key, None)
        print(f"ERROR reason = {exc}")
        return

    if isinstance(isotp_frame, SingleFrame):
        print(f"ISO-TP   [1/1]  type = SF  payload = {format_data(payload)}")
        print(f"DONE     payload = {format_data(payload)}")
        print(DONE_SEPARATOR)
        return

    if isinstance(isotp_frame, FirstFrame):
        state = sequence_states[key]
        print(
            "ISO-TP   "
            f"[{state['current']}/{state['total']}]  "
            "type = FF  "
            f"total = {isotp_frame.total_length}  "
            f"initial = {format_data(isotp_frame.payload)}"
        )
        return

    if isinstance(isotp_frame, ConsecutiveFrame):
        state = sequence_states.get(key, {"current": 1, "total": 1})
        print(
            "ISO-TP   "
            f"[{state['current']}/{state['total']}]  "
            "type = CF  "
            f"sn = {isotp_frame.sequence_number}  "
            f"chunk = {format_data(isotp_frame.payload)}"
        )

        if payload is None:
            return

        print(f"DONE     payload = {format_data(payload)}")
        print(DONE_SEPARATOR)
        sequence_states.pop(key, None)
        return

    print(f"ERROR reason = Unsupported parsed frame: {type(isotp_frame).__name__}")


def main():
    bus = can.Bus(interface="socketcan", channel=CHANNEL)
    reassemblers = {}
    sequence_states = {}

    print_header()

    try:
        while True:
            msg = bus.recv(timeout=1.0)

            if msg is None:
                continue

            frame = parse_python_can_message(msg)
            log_isotp_frame(frame, reassemblers, sequence_states)

    except KeyboardInterrupt:
        print("\nStopped program.")

    finally:
        bus.shutdown()


if __name__ == "__main__":
    main()
