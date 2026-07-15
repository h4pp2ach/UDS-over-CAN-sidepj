from pathlib import Path
import sys
import time

import can

PROJECT_ROOT = Path(__file__).resolve().parent
SRC_DIR = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

from isotp_transport_demo_common import (
    describe_isotp_frame,
    format_data,
)
from isotp_frame import (
    ConsecutiveFrame,
    FirstFrame,
    FlowControlFrame,
    parse_isotp_frame,
)
from isotp_transport_layer import (
    IsoTpFlowControlError,
    IsoTpProtocolError,
    IsoTpTransportError,
    IsoTpTransportLayer,
)

CHANNEL = "vcan0"
REQUEST_CAN_ID = 0x7E0
RESPONSE_CAN_ID = 0x7E8
TX_DATA_LENGTH = 8
PADDING_BYTE = 0x00
TRANSPORT_FRAME_TIMEOUT_SECONDS = 1.0
SEPARATOR_WIDTH = 88

LONG_PAYLOAD = bytes.fromhex(
    "36 01 "
    "49 53 4F 54 50 5F 44 45 "
    "4D 4F 5F 4C 4F 4E 47 5F "
    "50 41 59 4C 4F 41 44 5F "
    "42 4C 4F 43 4B 5F 30 30 "
    "30 31 5F 41 42 43 44 45 "
    "46 47 48 49 4A 4B 4C 4D"
)


def main() -> None:
    print_header()

    print("Payload")
    print("-" * 88)
    print(f"- length : {len(LONG_PAYLOAD)} bytes")
    print(f"- data   : {format_data(LONG_PAYLOAD)}")
    print()
    input("receiver가 대기 중이면 Enter를 눌러 전송을 시작하세요. ")

    bus = VcanSenderTraceBus(can.Bus(interface="socketcan", channel=CHANNEL))

    transport = IsoTpTransportLayer.for_client(
        bus,
        request_can_id=REQUEST_CAN_ID,
        response_can_id=RESPONSE_CAN_ID,
        frame_timeout_seconds=TRANSPORT_FRAME_TIMEOUT_SECONDS,
        tx_data_length=TX_DATA_LENGTH,
        padding_byte=PADDING_BYTE,
    )

    try:
        transport.send(LONG_PAYLOAD)
    except KeyboardInterrupt:
        print("\n중단했습니다.")
    except IsoTpFlowControlError as exc:
        print(f"\nFLOW CONTROL ERROR: {exc}")
    except (IsoTpProtocolError, IsoTpTransportError) as exc:
        print(f"\nISO-TP ERROR: {exc}")
    else:
        print("\n전송 완료: receiver terminal에서 payload 완료 로그를 확인하세요.")
    finally:
        bus.shutdown()


def print_header() -> None:
    print("=" * SEPARATOR_WIDTH)
    print("ISO-TP Transport Layer vcan Sender Demo")
    print("=" * SEPARATOR_WIDTH)
    print("다른 터미널에서 receiver demo를 먼저 실행하세요.")
    print()


class VcanSenderTraceBus:
    def __init__(self, bus) -> None:
        self._bus = bus
        self._started_at = time.monotonic()
        self._last_event_at = self._started_at

    def send(self, msg) -> None:
        frame = self._parse_frame(msg)

        if isinstance(frame, FirstFrame):
            print_section("First Frame sent")
            self._print_frame("TX", msg)
            print()
            print(f"- total payload : {frame.total_length} bytes")
            print(f"- initial data  : {format_data(frame.payload)}")
            print("- sender는 receiver의 Flow Control을 기다립니다.")
        else:
            self._print_frame("TX", msg)

            if isinstance(frame, ConsecutiveFrame):
                print(f"- Consecutive Frame SN={frame.sequence_number} 전송")

        self._bus.send(msg)

    def recv(self, timeout=None):
        print()
        print("Flow Control 대기 중...")
        print("- receiver terminal에서 FC를 입력하면 여기서 수신됩니다.")

        msg = self._bus.recv(timeout=timeout)

        if msg is not None:
            frame = self._parse_frame(msg)
            self._print_frame("RX", msg)

            if isinstance(frame, FlowControlFrame):
                print(
                    "- FC 수신: "
                    f"{frame.flow_status.name}, "
                    f"BS={frame.block_size}, "
                    f"STmin={frame.st_min}"
                )

        return msg

    def shutdown(self) -> None:
        self._bus.shutdown()

    def _print_frame(self, direction: str, msg) -> None:
        now = time.monotonic()
        elapsed_ms = (now - self._started_at) * 1000
        delta_ms = (now - self._last_event_at) * 1000
        self._last_event_at = now
        data = bytes(msg.data)
        print(
            f"[{elapsed_ms:9.3f} ms] "
            f"(+{delta_ms:8.3f} ms) "
            f"CLIENT {direction} "
            f"id = 0x{msg.arbitration_id:08X}  "
            f"dlc = {msg.dlc}  "
            f"data = {format_data(data):<23}  "
            f"{describe_isotp_frame(data)}"
        )

    def _parse_frame(self, msg):
        try:
            return parse_isotp_frame(bytes(msg.data))
        except ValueError:
            return None


def print_section(title: str) -> None:
    print()
    print("-" * SEPARATOR_WIDTH)
    print(title)
    print("-" * SEPARATOR_WIDTH)


if __name__ == "__main__":
    main()
