import can
from python_can_parser import parse_python_can_message

def test_parse_python_can_message():
    msg = can.Message(arbitration_id=0x7E8,
                      data=[0x02, 0x10, 0x01, 0xAA, 0xAA, 0xAA, 0xAA, 0xAA],
                      dlc=8,
                      channel='can0')

    frame = parse_python_can_message(msg)

    assert frame.channel == "can0"
    assert frame.can_id == 0x7E8
    assert frame.dlc == 8
    assert frame.data == bytes([0x02, 0x10, 0x01, 0xAA, 0xAA, 0xAA, 0xAA, 0xAA])