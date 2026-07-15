MAX_CLASSIC_CAN_DATA_LENGTH = 8
MIN_ISOTP_CAN_DATA_LENGTH = 3
MAX_ISOTP_PAYLOAD_LENGTH = 0xFFF


def segment_isotp_payload(
    payload: bytes,
    *,
    tx_data_length: int = MAX_CLASSIC_CAN_DATA_LENGTH,
) -> list[bytes]:
    validate_tx_data_length(tx_data_length)

    if not payload:
        raise ValueError("ISO-TP payload must not be empty")

    if len(payload) > MAX_ISOTP_PAYLOAD_LENGTH:
        raise ValueError("ISO-TP payload supports up to 4095 bytes")

    max_single_frame_payload_length = tx_data_length - 1
    max_first_frame_payload_length = tx_data_length - 2
    max_consecutive_frame_payload_length = tx_data_length - 1

    if len(payload) <= max_single_frame_payload_length:
        return [bytes([len(payload)]) + payload]

    total_length = len(payload)
    first_pci = 0x10 | ((total_length >> 8) & 0x0F)
    first_frame = bytes([first_pci, total_length & 0xFF])
    first_frame += payload[:max_first_frame_payload_length]

    frames = [first_frame]
    offset = max_first_frame_payload_length
    sequence_number = 1

    while offset < total_length:
        chunk = payload[offset:offset + max_consecutive_frame_payload_length]
        frames.append(bytes([0x20 | sequence_number]) + chunk)
        offset += max_consecutive_frame_payload_length
        sequence_number = (sequence_number + 1) % 16

    return frames


def validate_tx_data_length(tx_data_length: int) -> None:
    if type(tx_data_length) is not int:
        raise TypeError("tx_data_length must be an integer")

    if (
        tx_data_length < MIN_ISOTP_CAN_DATA_LENGTH
        or tx_data_length > MAX_CLASSIC_CAN_DATA_LENGTH
    ):
        raise ValueError("tx_data_length must be between 3 and 8")
