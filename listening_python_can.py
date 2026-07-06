from pathlib import Path
import sys
import can

PROJECT_ROOT = Path(__file__).resolve().parent
SRC_DIR = PROJECT_ROOT / "src"
CHANNEL = "vcan0"
CHANNEL_COLUMN_WIDTH = 6
CAN_ID_COLUMN_WIDTH = 10
DLC_COLUMN_WIDTH = 3
DATA_COLUMN_WIDTH = 23

sys.path.insert(0, str(SRC_DIR))
from python_can_parser import parse_python_can_message


def format_data(data: bytes) -> str:
    return data.hex(" ").upper()


def table_border() -> str:
    return (
        f"+-{'-' * CHANNEL_COLUMN_WIDTH}-"
        f"+-{'-' * CAN_ID_COLUMN_WIDTH}-"
        f"+-{'-' * DLC_COLUMN_WIDTH}-"
        f"+-{'-' * DATA_COLUMN_WIDTH}-+"
    )


def print_header():
    print(f"Listening on {CHANNEL}")
    print("Press Ctrl+C to stop.")
    print()
    print(table_border())
    print(
        f"| {'CH':<{CHANNEL_COLUMN_WIDTH}} "
        f"| {'CAN ID':<{CAN_ID_COLUMN_WIDTH}} "
        f"| {'DLC':>{DLC_COLUMN_WIDTH}} "
        f"| {'DATA':<{DATA_COLUMN_WIDTH}} |"
    )
    print(table_border())


def format_frame_row(frame) -> str:
    data_text = format_data(frame.data)

    return (
        f"| {frame.channel:<{CHANNEL_COLUMN_WIDTH}} "
        f"| 0x{frame.can_id:08X} "
        f"| {frame.dlc:>{DLC_COLUMN_WIDTH}} "
        f"| {data_text:<{DATA_COLUMN_WIDTH}} |"
    )


def main():
    bus = can.Bus(interface="socketcan", channel=CHANNEL)
    
    print_header()

    try:
        while True:
            msg = bus.recv(timeout = 1.0)

            if msg is None:
                continue

            frame = parse_python_can_message(msg)
            
            print(format_frame_row(frame))
        
    except KeyboardInterrupt:
        print(table_border())
        print("\n Stopped program.")
    
    finally:
        bus.shutdown()

if __name__ == "__main__":
    main()
