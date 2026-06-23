from frame import CANFrame

def parse_candump_line(line: str) -> CANFrame:
    parts = line.split()
    
    channel = parts[0]
    can_id = int(parts[1], 16)
    dlc = int(parts[2].strip('[]'))
    data = bytes(int(x, 16) for x in parts[3:3+dlc])

    return CANFrame(channel=channel, can_id=can_id, dlc=dlc, data=data)