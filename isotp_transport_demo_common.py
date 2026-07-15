import time

from isotp_frame import (
    ConsecutiveFrame,
    FirstFrame,
    FlowControlFrame,
    SingleFrame,
    parse_isotp_frame,
)

DEMO_CONTROL_SERVICE_ID = 0xF0


class TraceBus:
    def __init__(self, bus, *, label: str) -> None:
        self._bus = bus
        self._label = label
        self._started_at = time.monotonic()
        self._last_event_at = self._started_at

    def send(self, msg) -> None:
        self._print_frame("TX", msg)
        self._bus.send(msg)

    def recv(self, timeout=None):
        msg = self._bus.recv(timeout=timeout)

        if msg is not None:
            self._print_frame("RX", msg)

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
            f"{self._label:<6} {direction} "
            f"id = 0x{msg.arbitration_id:08X}  "
            f"dlc = {msg.dlc}  "
            f"data = {format_data(data):<23}  "
            f"{describe_isotp_frame(data)}"
        )


def prompt_text(prompt: str, default: str) -> str:
    value = input(f"{prompt} [{default}]> ").strip()

    if not value:
        return default

    return value


def prompt_int(prompt: str, default: int) -> int:
    return int(prompt_text(prompt, f"0x{default:X}"), 0)


def prompt_float(prompt: str, default: float) -> float:
    return float(prompt_text(prompt, str(default)))


def prompt_padding_byte(default: int | None) -> int | None:
    default_text = "none" if default is None else f"0x{default:02X}"
    value = prompt_text("padding byte (none/off allowed)", default_text)

    if value.lower() in {"none", "off", "no"}:
        return None

    return int(value, 0)


def format_data(data: bytes) -> str:
    return data.hex(" ").upper()


def format_can_id(can_id: int) -> str:
    return f"0x{can_id:08X}"


def format_padding(padding_byte: int | None) -> str:
    if padding_byte is None:
        return "none"

    return f"0x{padding_byte:02X}"


def describe_isotp_frame(data: bytes) -> str:
    try:
        frame = parse_isotp_frame(data)
    except ValueError as exc:
        return f"ISO-TP parse error: {exc}"

    if isinstance(frame, SingleFrame):
        return f"SF len = {frame.length} payload = {format_data(frame.payload)}"

    if isinstance(frame, FirstFrame):
        return f"FF total = {frame.total_length} initial = {format_data(frame.payload)}"

    if isinstance(frame, ConsecutiveFrame):
        return f"CF SN = {frame.sequence_number} chunk = {format_data(frame.payload)}"

    if isinstance(frame, FlowControlFrame):
        return (
            "FC "
            f"status = {frame.flow_status.name} "
            f"BS = {frame.block_size} "
            f"ST_MIN = {frame.st_min} ms "
        )

    return f"Unsupported frame: {type(frame).__name__}"
