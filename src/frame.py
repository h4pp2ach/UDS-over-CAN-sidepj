from dataclasses import dataclass

@dataclass
class CANFrame:
    channel: str
    can_id: int
    dlc: int
    data: bytes