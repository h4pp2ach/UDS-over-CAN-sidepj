import can

CHANNEL = "vcan0"
MAX_CLASSIC_CAN_DATA_LENGTH = 8


def parse_user_input(line: str) -> can.Message:
    parts = line.split()

    if not parts:
        raise ValueError("input is empty")

    try:
        can_id = int(parts[0], 16)
    except ValueError as exc:
        raise ValueError("CAN ID must be a hexadecimal value") from exc

    if can_id < 0 or can_id > 0x1FFFFFFF:
        raise ValueError("CAN ID must be between 0 and 1FFFFFFF")

    data = []
    for token in parts[1:]:
        try:
            byte = int(token, 16)
        except ValueError as exc:
            raise ValueError(f"data byte '{token}' must be hexadecimal") from exc

        if byte < 0 or byte > 0xFF:
            raise ValueError(f"data byte '{token}' must be between 00 and FF")

        data.append(byte)

    if len(data) > MAX_CLASSIC_CAN_DATA_LENGTH:
        raise ValueError("classic CAN supports up to 8 data bytes")

    return can.Message(
        arbitration_id=can_id,
        data=data,
        is_extended_id=can_id > 0x7FF,
        check=True,
    )


def main():
    bus = can.Bus(interface="socketcan", channel=CHANNEL)

    print(f"Sending on {CHANNEL}")
    print("Input format: <can_id_hex> <data_hex...>")
    print("Example: 7E0 02 10 01 AA AA AA AA AA")
    print("Type q, quit, or exit to stop.")

    try:
        while True:
            try:
                line = input(f"{CHANNEL}> ").strip()
            except EOFError:
                print()
                break

            if not line:
                continue

            if line.lower() in {"q", "quit", "exit"}:
                break

            try:
                msg = parse_user_input(line)
            except ValueError as exc:
                print(f"Invalid input: {exc}")
                continue

            try:
                bus.send(msg)
            except can.CanError as exc:
                print(f"Send failed: {exc}")
                continue

            data_text = bytes(msg.data).hex(" ").upper()
            print(f"Sent {msg.arbitration_id:X} [{msg.dlc}] {data_text}")

    except KeyboardInterrupt:
        print("\nStopped program.")

    finally:
        bus.shutdown()


if __name__ == "__main__":
    main()
