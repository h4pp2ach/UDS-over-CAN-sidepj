from dataclasses import dataclass
from pathlib import Path
import sys
import time

import can

PROJECT_ROOT = Path(__file__).resolve().parent
SRC_DIR = PROJECT_ROOT / "src"
CHANNEL = "vcan0"
FRAME_DELAY_SECONDS = 0.5

sys.path.insert(0, str(SRC_DIR))
from isotp_tx_segmenter import segment_isotp_payload


@dataclass(frozen=True)
class IsoTpScenario:
    key: str
    name: str
    can_id: int
    payload: bytes


SCENARIOS = [
    IsoTpScenario(
        key="1",
        name="Single Frame - Diagnostic Session Control",
        can_id=0x100,
        payload=bytes.fromhex(
            "10 02"
        ),
    ),
    IsoTpScenario(
        key="2",
        name="Multi Frame - short TransferData payload (FF + CF 1)",
        can_id=0x100,
        payload=bytes.fromhex(
            "36 01 42 4C 4B 30 31 44 "
            "41 54 41"
        ),
    ),
    IsoTpScenario(
        key="3",
        name="Multi Frame - medium TransferData payload (FF + CF 2)",
        can_id=0x200,
        payload=bytes.fromhex(
            "36 02 46 57 5F 4D 45 54 "
            "41 5F 32 30 32 36 30 37 "
            "30 37 41 31"
        ),
    ),
    IsoTpScenario(
        key="4",
        name="Multi Frame - long TransferData payload (FF + CF 4)",
        can_id=0x200,
        payload=bytes.fromhex(
            "36 03 46 57 42 4C 4B 30 "
            "33 5F 41 44 44 52 30 30 "
            "30 30 33 30 30 30 5F 53 "
            "49 5A 45 30 30 33 32 5F "
            "4F 4B"
        ),
    ),
]


def create_can_message(can_id: int, data: bytes) -> can.Message:
    return can.Message(
        arbitration_id=can_id,
        data=data,
        is_extended_id=can_id > 0x7FF,
        check=True,
    )


def build_isotp_messages(can_id: int, payload: bytes) -> list[can.Message]:
    return [
        create_can_message(can_id, frame_data)
        for frame_data in segment_isotp_payload(payload)
    ]


def find_scenario(choice: str) -> IsoTpScenario:
    for scenario in SCENARIOS:
        if scenario.key == choice:
            return scenario

    raise ValueError(f"Unknown scenario: {choice}")


def print_menu():
    print()
    print("ISO-TP scenarios")
    for scenario in SCENARIOS:
        print(f"{scenario.key}. {scenario.name}")
    print("q. quit")


def send_scenario(bus, scenario: IsoTpScenario):
    messages = build_isotp_messages(scenario.can_id, scenario.payload)

    print(f"Scenario: {scenario.name}")
    print(f"Payload: {scenario.payload.hex(' ').upper()}")

    for index, msg in enumerate(messages, start=1):
        bus.send(msg)
        data_text = bytes(msg.data).hex(" ").upper()
        print(
            f"Sent [{index}/{len(messages)}] "
            f"id = 0x{msg.arbitration_id:08X}  "
            f"dlc = {msg.dlc}  "
            f"data = {data_text}"
        )

        if index < len(messages):
            time.sleep(FRAME_DELAY_SECONDS)
    
    time.sleep(1)


def main():
    bus = can.Bus(interface="socketcan", channel=CHANNEL)

    print(f"Sending ISO-TP scenarios on {CHANNEL}")

    try:
        while True:
            print_menu()
            choice = input(f"{CHANNEL} scenario> ").strip()

            if choice.lower() in {"q", "quit", "exit"}:
                break

            try:
                scenario = find_scenario(choice)
            except ValueError as exc:
                print(f"Invalid input: {exc}")
                continue

            try:
                send_scenario(bus, scenario)
            except can.CanError as exc:
                print(f"Send failed: {exc}")

    except (EOFError, KeyboardInterrupt):
        print("\nStopped program.")

    finally:
        bus.shutdown()


if __name__ == "__main__":
    main()
