from pathlib import Path
import sys
import time

import can

PROJECT_ROOT = Path(__file__).resolve().parent
SRC_DIR = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

from isotp_frame import ConsecutiveFrame, FirstFrame, FlowStatus, SingleFrame
from isotp_frame import parse_isotp_frame
from isotp_payload_reassembler import IsoTpPayloadReassembler
from isotp_transport_demo_common import (
    describe_isotp_frame,
    format_can_id,
    format_data,
)
from isotp_transport_layer import build_flow_control_data, decode_st_min_seconds

CHANNEL = "vcan0"
REQUEST_CAN_ID = 0x7E0
RESPONSE_CAN_ID = 0x7E8
TX_DATA_LENGTH = 8
PADDING_BYTE = 0x00
RECV_TIMEOUT_SECONDS = 1.0
SEPARATOR_WIDTH = 88


class InteractiveVcanReceiver:
    def __init__(
        self,
        bus,
        *,
        request_can_id: int,
        response_can_id: int,
        tx_data_length: int,
        padding_byte: int | None,
    ) -> None:
        self.bus = bus
        self.request_can_id = request_can_id
        self.response_can_id = response_can_id
        self.tx_data_length = tx_data_length
        self.padding_byte = padding_byte
        self.started_at = None
        self.last_event_at = None
        self.reassembler = IsoTpPayloadReassembler()
        self.block_size = 0
        self.received_in_block = 0
        self.fc_count = 0

    def run(self) -> None:
        self.started_at = time.monotonic()
        self.last_event_at = self.started_at
        self._print_waiting()

        while True:
            msg = self.bus.recv(timeout=RECV_TIMEOUT_SECONDS)

            if msg is None:
                continue

            if msg.arbitration_id != self.request_can_id:
                print(
                    f"SKIP unrelated CAN ID: {format_can_id(msg.arbitration_id)}"
                )
                continue

            try:
                frame = parse_isotp_frame(bytes(msg.data))
            except ValueError as exc:
                self._print_frame("RX", msg)
                print(f"ERROR ISO-TP parse failed: {exc}")
                self._reset_reassembly()
                continue

            try:
                self._handle_frame(frame, msg)
            except KeyboardInterrupt:
                raise
            except ValueError as exc:
                print(f"ERROR reassembly failed: {exc}")
                self._reset_reassembly()

    def _handle_frame(self, frame, msg) -> None:
        if isinstance(frame, SingleFrame):
            self._print_frame("RX", msg)
            payload = self.reassembler.feed(frame)
            self._print_done(payload)
            self._reset_reassembly()
            return

        if isinstance(frame, FirstFrame):
            self._handle_first_frame(frame, msg)
            return

        if isinstance(frame, ConsecutiveFrame):
            self._print_frame("RX", msg)
            self._handle_consecutive_frame(frame)
            return

        self._print_frame("RX", msg)
        print(f"SKIP unsupported frame from sender: {type(frame).__name__}")

    def _handle_first_frame(self, frame: FirstFrame, msg) -> None:
        self._reset_reassembly()
        self.reassembler.feed(frame)
        remaining_length = frame.total_length - len(frame.payload)

        print_section("First Frame received")
        self._print_frame("RX", msg)
        print()
        print(f"- total payload : {frame.total_length} bytes")
        print(f"- initial data  : {format_data(frame.payload)}")
        print(f"- remaining     : {remaining_length} bytes")
        print("- sender는 FC를 받을 때까지 CF를 보내지 않습니다.")

        input("\n확인 후 Enter를 누르면 FC 입력으로 이동합니다. ")
        self._prompt_and_send_flow_control()

    def _handle_consecutive_frame(self, frame: ConsecutiveFrame) -> None:
        payload = self.reassembler.feed(frame)

        if payload is not None:
            self._print_done(payload)
            self._reset_reassembly()
            return

        self.received_in_block += 1

        if self.block_size == 0:
            return

        if self.received_in_block < self.block_size:
            return

        print_section("Block completed")
        print(f"- received CF in block : {self.received_in_block}")
        print("- sender는 다음 FC를 받을 때까지 멈춥니다.")
        input("\n확인 후 Enter를 누르면 다음 FC 입력으로 이동합니다. ")
        self._prompt_and_send_flow_control()

    def _prompt_and_send_flow_control(self) -> None:
        while True:
            action = self._prompt_flow_control_action()

            if action == "continue":
                print()
                print("CTS 값 입력")
                print("- BS: 0이면 남은 CF 전체 허용, 1..255면 해당 개수 후 다시 FC 입력")
                print("- STmin: CF 사이 최소 지연 시간(ms)")
                block_size = prompt_decimal_byte("BS 입력 (0..255)> ")
                st_min_ms = prompt_decimal_byte(
                    "STmin 입력(ms, 0..127)> ",
                    max_value=0x7F,
                )
                self._send_flow_control(
                    FlowStatus.CONTINUE_TO_SEND,
                    block_size=block_size,
                    st_min=st_min_ms,
                )
                self.block_size = block_size
                self.received_in_block = 0
                return

            if action == "wait":
                self._send_flow_control(
                    FlowStatus.WAIT,
                    block_size=0,
                    st_min=0,
                )
                print("- WAIT 전송 완료: sender는 다음 FC를 계속 기다립니다.")
                continue

            if action == "overflow":
                self._send_flow_control(
                    FlowStatus.OVERFLOW,
                    block_size=0,
                    st_min=0,
                )
                print("- OVERFLOW 전송 완료: sender는 전송을 중단합니다.")
                self._reset_reassembly()
                return

    def _prompt_flow_control_action(self) -> str:
        self.fc_count += 1
        print_section(f"FC #{self.fc_count} 입력")
        print("- c : CTS, BS/STmin 입력 후 CF 수신 허용")
        print("- w : WAIT, sender가 계속 대기")
        print("- o : OVERFLOW, sender 전송 중단")
        print("- q : 종료")

        while True:
            command = input("FC 상태 선택 (c/w/o/q)> ").strip().lower()

            if command in {"c", "cts", "continue"}:
                return "continue"

            if command in {"w", "wait"}:
                return "wait"

            if command in {"o", "overflow"}:
                return "overflow"

            if command in {"q", "quit", "exit"}:
                raise KeyboardInterrupt

            print("잘못된 입력: c, w, o, q 중 하나를 입력하세요.")

    def _send_flow_control(
        self,
        flow_status: FlowStatus,
        *,
        block_size: int,
        st_min: int,
    ) -> None:
        data = build_flow_control_data(
            flow_status,
            block_size=block_size,
            st_min=st_min,
        )
        data = self._pad_can_data(data)
        msg = can.Message(
            arbitration_id=self.response_can_id,
            data=data,
            is_extended_id=False,
            check=True,
        )

        if flow_status == FlowStatus.CONTINUE_TO_SEND:
            st_min_seconds = decode_st_min_seconds(st_min)
            print()
            print(
                f"FC 적용: CTS, BS={block_size}, STmin={st_min} ms "
                f"({st_min_seconds * 1000:.3f} ms)"
            )

        self._print_frame("TX", msg)
        self.bus.send(msg)

    def _pad_can_data(self, data: bytes) -> bytes:
        if self.padding_byte is None:
            return data

        return data + bytes([self.padding_byte]) * (self.tx_data_length - len(data))

    def _print_waiting(self) -> None:
        print()
        print("수신 대기 중")
        print("-" * SEPARATOR_WIDTH)
        print("다른 터미널에서 sender demo를 실행하세요.")

    def _print_done(self, payload: bytes) -> None:
        print_section("Payload completed")
        print(f"- length : {len(payload)} bytes")
        print(f"- data   : {format_data(payload)}")
        print("-" * SEPARATOR_WIDTH)

    def _reset_reassembly(self) -> None:
        self.reassembler = IsoTpPayloadReassembler()
        self.block_size = 0
        self.received_in_block = 0

    def _print_frame(self, direction: str, msg) -> None:
        now = time.monotonic()

        if self.started_at is None:
            self.started_at = now

        if self.last_event_at is None:
            self.last_event_at = now

        elapsed_ms = (now - self.started_at) * 1000
        delta_ms = (now - self.last_event_at) * 1000
        self.last_event_at = now
        data = bytes(msg.data)
        print(
            f"[{elapsed_ms:9.3f} ms] "
            f"(+{delta_ms:8.3f} ms) "
            f"SERVER {direction} "
            f"id = 0x{msg.arbitration_id:08X}  "
            f"dlc = {msg.dlc}  "
            f"data = {format_data(data):<23}  "
            f"{describe_isotp_frame(data)}"
        )


def main() -> None:
    print_header()

    bus = can.Bus(interface="socketcan", channel=CHANNEL)
    receiver = InteractiveVcanReceiver(
        bus,
        request_can_id=REQUEST_CAN_ID,
        response_can_id=RESPONSE_CAN_ID,
        tx_data_length=TX_DATA_LENGTH,
        padding_byte=PADDING_BYTE,
    )

    try:
        receiver.run()
    except KeyboardInterrupt:
        print("\n중단했습니다.")
    finally:
        bus.shutdown()


def print_header() -> None:
    print("=" * SEPARATOR_WIDTH)
    print("ISO-TP Transport Layer vcan Interactive Receiver")
    print("=" * SEPARATOR_WIDTH)
    print("First Frame을 받은 뒤 확인을 기다리고, 이후 FC를 직접 입력합니다.")


def print_section(title: str) -> None:
    print()
    print("-" * SEPARATOR_WIDTH)
    print(title)
    print("-" * SEPARATOR_WIDTH)


def prompt_decimal_byte(prompt: str, *, max_value: int = 0xFF) -> int:
    while True:
        raw_value = input(prompt).strip()

        try:
            value = int(raw_value, 10)
        except ValueError:
            print("잘못된 입력: 10진수 숫자로 입력하세요.")
            continue

        if value < 0 or value > max_value:
            print(f"잘못된 입력: 0..{max_value} 범위로 입력하세요.")
            continue

        return value


if __name__ == "__main__":
    main()
