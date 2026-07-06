from frame import CANFrame

def parse_python_can_message(msg) -> CANFrame:

    channel=str(msg.channel)
    can_id=msg.arbitration_id
    dlc=msg.dlc
    data=bytes(msg.data)

    return CANFrame(channel=channel, can_id=can_id, dlc=dlc, data=data)