from candump_parser import parse_candump_line


# 기능 그룹: candump 텍스트 파싱
# 검증 목적: candump 한 줄에서 채널, CAN ID, DLC, 데이터가 정확히 변환되는지 확인한다.
def test_parse_candump_line():
    line = "can0 7E8 [8] 02 10 01 AA AA AA AA AA"

    frame = parse_candump_line(line)

    assert frame.channel == "can0"
    assert frame.can_id == 0x7E8
    assert frame.dlc == 8
    assert frame.data == bytes([0x02, 0x10, 0x01, 0xAA, 0xAA, 0xAA, 0xAA, 0xAA])
