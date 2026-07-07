# UDS-over-CAN-sidepj

Python 기반으로 CAN, ISO-TP, UDS, 그리고 UDS 기반 Firmware Download 과정을 직접 구현하며 학습하는 사이드 프로젝트입니다.


## Goal

이 프로젝트의 최종 목표는 가상 CAN 환경에서 UDS 기반 펌웨어 다운로드 시퀀스를 구현하는 것입니다.

최종적으로 다음 흐름을 구현합니다.

```text
Firmware Binary
    ↓드
UDS Flashing Client
    ↓
ISO-TP Transport Layer
    ↓
SocketCAN / vcan0
    ↓
Virtual ECU
    ↓
Virtual Flash Memory
```

## Environment

- Ubuntu 22.04 or later
- Python 3.10+
- SocketCAN
- can-utils
- python-can
- pytest

## Project Structure

```text
.
├── README.md
├── docs/
├── requirements.txt
├── pyproject.toml
├── setup_vcan.sh
├── del_vcan.sh
├── listening_python_can.py
├── send_python_can.py
├── src/
└── tests/
```

## Documentation

- [Step 1. CAN Frame Parser](docs/01_can_frame_parser.md)

## Current Status

- `CANFrame` 공통 데이터 구조 정의
- `candump` 텍스트 로그 parser 구현
- `python-can` 메시지 parser 구현
- `vcan0` 송신/수신 예제 스크립트 구현
- parser 단위 테스트 구성

## Roadmap

- Step 1. CAN Frame Parser
- Step 2. ISO-TP Transport Layer
- Step 3. UDS Diagnostic Service Handling
- Step 4. UDS Flashing Client
- Step 5. Virtual ECU
- Step 6. Virtual Flash Memory
- Step 7. Firmware Download Sequence
