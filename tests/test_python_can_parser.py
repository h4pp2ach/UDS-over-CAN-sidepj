import can
from python_can_parser import parse_python_can_message


# 기능 그룹: python-can 메시지 파싱
# 검증 목적: python-can 메시지가 내부 CAN 프레임 필드로 손실 없이 변환되는지 확인한다.
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
