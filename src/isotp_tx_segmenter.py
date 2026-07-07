MAX_SINGLE_FRAME_PAYLOAD_LENGTH = 7
MAX_FIRST_FRAME_PAYLOAD_LENGTH = 6
MAX_CONSECUTIVE_FRAME_PAYLOAD_LENGTH = 7
MAX_ISOTP_PAYLOAD_LENGTH = 0xFFF


def segment_isotp_payload(payload: bytes) -> list[bytes]:
    if not payload:
        raise ValueError("ISO-TP payload must not be empty")

    if len(payload) > MAX_ISOTP_PAYLOAD_LENGTH:
        raise ValueError("ISO-TP payload supports up to 4095 bytes")

    if len(payload) <= MAX_SINGLE_FRAME_PAYLOAD_LENGTH:
        return [bytes([len(payload)]) + payload]

    total_length = len(payload)
    first_pci = 0x10 | ((total_length >> 8) & 0x0F)
    first_frame = bytes([first_pci, total_length & 0xFF])
    first_frame += payload[:MAX_FIRST_FRAME_PAYLOAD_LENGTH]

    frames = [first_frame]
    offset = MAX_FIRST_FRAME_PAYLOAD_LENGTH
    sequence_number = 1

    while offset < total_length:
        chunk = payload[offset:offset + MAX_CONSECUTIVE_FRAME_PAYLOAD_LENGTH]
        frames.append(bytes([0x20 | sequence_number]) + chunk)
        offset += MAX_CONSECUTIVE_FRAME_PAYLOAD_LENGTH
        sequence_number = (sequence_number + 1) % 16

    return frames
