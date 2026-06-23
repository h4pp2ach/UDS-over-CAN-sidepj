from candump_parser import parse_candump_line

def test_parse_candump_line():
    line = "can0 7E8 [8] 02 10 01 AA AA AA AA AA"

    frame = parse_candump_line(line)

    assert frame.channel == "can0"
    assert frame.can_id == 0x7E8
    assert frame.dlc == 8
    assert frame.data == bytes([0x02, 0x10, 0x01, 0xAA, 0xAA, 0xAA, 0xAA, 0xAA])