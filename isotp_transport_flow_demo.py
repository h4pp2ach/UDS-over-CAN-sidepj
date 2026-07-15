from pathlib import Path
import select
import sys
import time

import can

PROJECT_ROOT = Path(__file__).resolve().parent
SRC_DIR = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

from isotp_frame import FirstFrame, FlowStatus, parse_isotp_frame
from isotp_transport_demo_common import (
    describe_isotp_frame,
    format_can_id,
    format_data,
    format_padding,
)
from isotp_transport_layer import (
    IsoTpFlowControlError,
    IsoTpProtocolError,
    IsoTpTimeoutError,
    IsoTpTransportError,
    IsoTpTransportLayer,
    build_flow_control_data,
    decode_st_min_seconds,
)

REQUEST_CAN_ID = 0x7E0
RESPONSE_CAN_ID = 0x7E8
TX_DATA_LENGTH = 8
PADDING_BYTE = 0x00
FRAME_TIMEOUT_SECONDS = 30.0
EXAMPLE_BLOCK_SIZE = 3
EXAMPLE_ST_MIN_MS = 50
DISPLAY_PAUSE_SECONDS = 1.0
DATA_BYTES_PER_LINE = 8
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

    bus = InteractiveFlowControlBus(
        response_can_id=RESPONSE_CAN_ID,
        tx_data_length=TX_DATA_LENGTH,
        padding_byte=PADDING_BYTE,
    )
    transport = IsoTpTransportLayer.for_client(
        bus,
        request_can_id=REQUEST_CAN_ID,
        response_can_id=RESPONSE_CAN_ID,
        frame_timeout_seconds=FRAME_TIMEOUT_SECONDS,
        tx_data_length=TX_DATA_LENGTH,
        padding_byte=PADDING_BYTE,
    )

    try:
        transport.send(LONG_PAYLOAD)
    except KeyboardInterrupt:
        print("\n중단했습니다.")
    except IsoTpTimeoutError as exc:
        print(f"\nTIMEOUT: {exc}")
    except IsoTpFlowControlError as exc:
        print(f"\nFLOW CONTROL ERROR: {exc}")
    except (IsoTpProtocolError, IsoTpTransportError) as exc:
        print(f"\nISO-TP ERROR: {exc}")
    else:
        print("\n전송 완료: FF 이후 FC 대기, STmin, BS 흐름을 확인해보세요.")


class InteractiveFlowControlBus:
    def __init__(
        self,
        *,
        response_can_id: int,
        tx_data_length: int,
        padding_byte: int | None,
    ) -> None:
        self.response_can_id = response_can_id
        self.tx_data_length = tx_data_length
        self.padding_byte = padding_byte
        self._started_at = time.monotonic()
        self._last_event_at = self._started_at
        self._presentation_pause_seconds = 0.0
        self._fc_count = 0
        self._printed_first_frame_context = False

    def send(self, msg) -> None:
        self._print_frame("SENDER", "TX", msg)

    def recv(self, timeout=None):
        self._fc_count += 1
        print_section(f"FC #{self._fc_count} 입력 - sender 대기 중", "-")
        print_kv(
            [
                ("상황", "sender가 Flow Control을 기다립니다."),
                ("FC 송신 CAN ID", format_can_id(self.response_can_id)),
                ("입력 순서", "FC 상태 c를 고르면 BS와 STmin(ms)을 입력합니다."),
            ]
        )

        if self._fc_count == 1:
            print()
            print("- 입력 힌트")
            print("  - c          : 계속 진행")
            print("  - w          : WAIT / sender는 계속 대기")
            print("  - o          : OVERFLOW / sender는 전송 중단")
            print("  - t          : FC 미수신 timeout 재현")
            print("  - q          : 예제 종료")
            print("  - BS=0       : 남은 CF 전체 허용")
            print("  - BS=1..255  : 해당 CF 개수 전송 후 다시 FC 대기")
            wait_to_continue("설명을 확인했으면 Enter를 눌러주세요.")

        while True:
            try:
                fc_data = self._prompt_flow_control_data(timeout)
            except RequestedTimeout:
                print("FC 미수신 timeout 상황을 재현합니다.")
                return None
            except ValueError as exc:
                print(f"잘못된 입력: {exc}")
                continue

            msg = can.Message(
                arbitration_id=self.response_can_id,
                data=fc_data,
                is_extended_id=False,
                check=True,
            )
            self._print_frame("RECEIVER", "TX", msg)
            return msg

    def _prompt_flow_control_data(self, timeout: float | None) -> bytes:
        raw_status = self._read_required_line(
            f"FC #{self._fc_count} 상태 선택 (c/w/o/t/q)> ",
            timeout,
        )
        command = raw_status.strip().lower()

        if command in {"q", "quit", "exit"}:
            raise KeyboardInterrupt

        if command in {"t", "timeout"}:
            raise RequestedTimeout

        if command in {"w", "wait"}:
            print("WAIT FC 전송: sender는 CF를 보내지 않고 다음 FC를 계속 기다립니다.")
            return self._pad_can_data(
                build_flow_control_data(
                    FlowStatus.WAIT,
                    block_size=0,
                    st_min=0,
                )
            )

        if command in {"o", "overflow"}:
            print("OVERFLOW FC 전송: sender는 전송을 중단해야 합니다.")
            return self._pad_can_data(
                build_flow_control_data(
                    FlowStatus.OVERFLOW,
                    block_size=0,
                    st_min=0,
                )
            )

        if command not in {"c", "cts", "continue"}:
            raise ValueError("FC 상태는 c, w, o, t, q 중 하나로 입력해야 합니다")

        block_size = self._prompt_required_decimal_byte(
            f"FC #{self._fc_count} BS 입력 (0..255)> ",
            timeout=timeout,
            name="BS",
        )
        st_min_ms = self._prompt_required_decimal_byte(
            f"FC #{self._fc_count} STmin 입력(ms, 0..127)> ",
            timeout=timeout,
            name="STmin ms",
            max_value=0x7F,
        )
        return self._build_continue_to_send(block_size, st_min_ms)

    def _prompt_required_decimal_byte(
        self,
        prompt: str,
        *,
        timeout: float | None,
        name: str,
        max_value: int = 0xFF,
    ) -> int:
        while True:
            raw_value = self._read_required_line(prompt, timeout)

            try:
                return parse_required_decimal_byte(
                    raw_value,
                    name=name,
                    max_value=max_value,
                )
            except ValueError as exc:
                print(f"잘못된 입력: {exc}")

    def _build_continue_to_send(self, block_size: int, st_min_ms: int) -> bytes:
        st_min = encode_st_min_ms(st_min_ms)
        data = build_flow_control_data(
            FlowStatus.CONTINUE_TO_SEND,
            block_size=block_size,
            st_min=st_min,
        )
        self._print_fc_effect(block_size, st_min_ms, st_min)
        return self._pad_can_data(data)

    def _read_required_line(self, prompt: str, timeout: float | None) -> str:
        value = read_line_with_timeout(prompt, timeout)

        if value is None:
            raise RequestedTimeout

        return value

    def _pad_can_data(self, data: bytes) -> bytes:
        if self.padding_byte is None:
            return data

        return data + bytes([self.padding_byte]) * (self.tx_data_length - len(data))

    def _print_frame(self, actor: str, direction: str, msg) -> None:
        now = self._log_time()
        elapsed_ms = (now - self._started_at) * 1000
        delta_ms = (now - self._last_event_at) * 1000
        self._last_event_at = now
        data = bytes(msg.data)

        if self._print_first_frame_context_if_needed(
            actor,
            direction,
            msg,
            data,
            elapsed_ms,
            delta_ms,
        ):
            return

        print()
        print(
            f"[{elapsed_ms:9.3f} ms] (+{delta_ms:8.3f} ms) {actor:<8} {direction}"
        )
        print(f"  - CAN     id={format_can_id(msg.arbitration_id)}  dlc={msg.dlc}")
        print("  - DATA")
        print_data_block(data, indent="      ")
        print(f"  - ISO-TP  {describe_isotp_frame(data)}")
        self._pause_for_readability()

    def _log_time(self) -> float:
        return time.monotonic() - self._presentation_pause_seconds

    def _print_first_frame_context_if_needed(
        self,
        actor: str,
        direction: str,
        msg,
        data: bytes,
        elapsed_ms: float,
        delta_ms: float,
    ) -> bool:
        if self._printed_first_frame_context:
            return False

        if actor != "SENDER" or direction != "TX":
            return False

        try:
            frame = parse_isotp_frame(data)
        except ValueError:
            return False

        if not isinstance(frame, FirstFrame):
            return False

        self._printed_first_frame_context = True
        remaining_length = frame.total_length - len(frame.payload)
        print_section("상황 #0 - First Frame", "-")
        print(
            f"[{elapsed_ms:9.3f} ms] (+{delta_ms:8.3f} ms) {actor:<8} {direction}"
        )
        print(f"  - CAN     id={format_can_id(msg.arbitration_id)}  dlc={msg.dlc}")
        print("  - DATA")
        print(f"      {format_data(data)}")
        print()
        print("- 이벤트       : sender가 First Frame을 보냈습니다.")
        print(
            f"- 전체 payload : {frame.total_length} bytes, "
            f"(0x{frame.total_length:03X} = {frame.total_length})"
        )
        print(
            f"- 이번 FF data : {len(frame.payload)} bytes, {format_data(frame.payload)}"
        )
        print(f"- 남은 data    : {remaining_length} bytes")
        print()
        print("- sender는 receiver의 FC를 받기 전까지 CF를 보내지 않습니다.")
        wait_to_continue("확인했으면 Enter를 눌러주세요.")
        return True

    def _pause_for_readability(self) -> None:
        self._sleep_for_presentation(DISPLAY_PAUSE_SECONDS)

    def _print_fc_effect(self, block_size: int, st_min_ms: int, st_min: int) -> None:
        st_min_seconds = decode_st_min_seconds(st_min)

        if block_size == 0:
            block_text = "남은 CF를 끝까지 보낼 수 있습니다."
        else:
            block_text = f"CF {block_size}개를 보낸 뒤 sender가 다시 FC를 기다립니다."

        print()
        print("FC 적용")
        print("-" * SEPARATOR_WIDTH)
        print("- status : CTS (Continue To Send)")
        print(f"- BS     : {block_size}")
        print(
            f"- STmin  : {st_min_ms} ms -> byte 0x{st_min:02X} "
            f"({st_min_seconds * 1000:.3f} ms)"
        )
        print(f"- effect : {block_text}")
        print("-" * SEPARATOR_WIDTH)

    def _sleep_for_presentation(self, seconds: float) -> None:
        if seconds <= 0 or not sys.stdout.isatty():
            return

        started_at = time.monotonic()
        time.sleep(seconds)
        self._presentation_pause_seconds += time.monotonic() - started_at


class RequestedTimeout(ValueError):
    pass


def print_header() -> None:
    print_section("ISO-TP Transport Layer Interactive Flow Demo", "=")
    print("이 예제는 실제 CAN 인터페이스 없이 sender와 receiver를 한 프로세스에서 재현합니다.")
    print()
    print_kv(
        [
            ("request CAN ID", f"{format_can_id(REQUEST_CAN_ID)}  sender(client) TX"),
            ("response CAN ID", f"{format_can_id(RESPONSE_CAN_ID)}  receiver(server) FC TX"),
            ("tx_data_length", str(TX_DATA_LENGTH)),
            ("padding_byte", format_padding(PADDING_BYTE)),
            ("FC timeout", f"{FRAME_TIMEOUT_SECONDS:.0f}s"),
            ("display pause", f"{DISPLAY_PAUSE_SECONDS:.2f}s per CAN frame"),
        ]
    )
    print()
    print("- 화면 표시용 frame pause는 로그의 +delta 계산에서 제외합니다.")
    wait_to_continue("설정을 확인했으면 Enter를 누르세요. ")

    print_section("확인할 흐름", "-")
    print("1. sender가 FF를 보낸 뒤 FC가 올 때까지 멈춥니다.")
    print("2. STmin 값을 키우면 CF 사이의 +delta 시간이 커집니다.")
    print("3. BS 값을 주면 해당 CF 개수마다 다시 FC 입력을 기다립니다.")
    wait_to_continue("흐름을 확인했으면 Enter를 누르세요. ")

    print_section("전송 payload", "-")
    print(f"- length: {len(LONG_PAYLOAD)} bytes")
    print("- data")
    print_data_block(LONG_PAYLOAD, indent="  ")
    print()
    wait_to_continue("payload를 확인했으면 Enter를 눌러 전송을 시작하세요. ")


def read_line_with_timeout(prompt: str, timeout: float | None) -> str | None:
    print(prompt, end="", flush=True)

    if not sys.stdin.isatty():
        line = sys.stdin.readline()

        if line == "":
            print()
            return None

        return line.rstrip("\n")

    readable, _, _ = select.select([sys.stdin], [], [], timeout)

    if not readable:
        print()
        return None

    return sys.stdin.readline().rstrip("\n")


def wait_to_continue(prompt: str) -> None:
    input(f"\n{prompt}")


def print_section(title: str, char: str) -> None:
    print()
    print(char * SEPARATOR_WIDTH)
    print(title)
    print(char * SEPARATOR_WIDTH)


def print_kv(rows: list[tuple[str, str]]) -> None:
    key_width = max(len(key) for key, _ in rows)

    for key, value in rows:
        print(f"- {key:<{key_width}} : {value}")


def print_data_block(data: bytes, *, indent: str) -> None:
    for offset in range(0, len(data), DATA_BYTES_PER_LINE):
        chunk = data[offset:offset + DATA_BYTES_PER_LINE]
        print(f"{indent}{offset:04X}: {chunk.hex(' ').upper()}")


def parse_required_decimal_byte(
    value: str,
    *,
    name: str,
    max_value: int = 0xFF,
) -> int:
    stripped_value = value.strip()

    if not stripped_value:
        raise ValueError(f"{name}을 입력해야 합니다")

    try:
        parsed_value = int(stripped_value, 10)
    except ValueError as exc:
        raise ValueError(f"{name}은 10진수 숫자로 입력해야 합니다") from exc

    if parsed_value < 0 or parsed_value > max_value:
        raise ValueError(f"{name}은 0..{max_value} 범위여야 합니다")

    return parsed_value


def encode_st_min_ms(st_min_ms: int) -> int:
    if st_min_ms < 0 or st_min_ms > 0x7F:
        raise ValueError("STmin ms는 0..127 범위여야 합니다")

    return st_min_ms


if __name__ == "__main__":
    main()
