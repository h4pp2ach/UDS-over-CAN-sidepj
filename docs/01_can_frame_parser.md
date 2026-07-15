# Step 1. CAN Frame Parser

이 문서는 현재 프로젝트의 첫 번째 단계인 CAN frame parser와 `vcan0` 기반 송수신 예제를 정리합니다.

현재 단계의 목적은 CAN 로그와 CANFrame 구조를 다뤄보는 것입니다.

- `candump` 텍스트 로그
- `python-can`의 `can.Message` 객체

<br />

## Environment Setup

이 프로젝트는 Linux SocketCAN 환경을 기준으로 합니다. `vcan` 인터페이스를 만들기 위해 `sudo`, `modprobe`, `ip link` 명령을 사용합니다.

### Python Virtual Environment

Python 가상환경을 만들고 의존성을 설치합니다.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

가상환경 활성화.

```bash
source .venv/bin/activate
```

### can-utils

Ubuntu 환경에서 `can-utils`를 설치합니다.

```bash
sudo apt update
sudo apt install can-utils
```

<br />

## Parser Roles

두 parser는 최종 결과로 같은 `CANFrame`을 반환하지만 입력이 다릅니다.

```text
candump_parser      : candump 텍스트 로그 -> CANFrame
python_can_parser   : python-can Message 객체 -> CANFrame
```

`listening_python_can.py`는 `python-can`으로 `vcan0`에서 직접 메시지를 받으므로 `python_can_parser`를 사용합니다.

반대로 `candump vcan0` 출력이나 저장된 candump 로그를 읽는 기능을 만들면 `candump_parser`를 사용합니다.

<br />

## Test

가상환경을 활성화한 뒤 pytest를 실행합니다.

```bash
source .venv/bin/activate
python -m pytest
```

현재 테스트는 다음 내용을 확인합니다.

- `candump` 문자열을 `CANFrame`으로 변환
- `python-can` 메시지를 `CANFrame`으로 변환

<br />

## vcan Setup

`vcan0`을 생성하고 활성화합니다.

```bash
./setup_vcan.sh
```

정상적으로 설정되면 마지막에 `vcan0` 인터페이스 정보가 출력됩니다.

상태를 직접 확인하려면 다음 명령을 사용할 수 있습니다.

```bash
ip link show vcan0
```

<br />

## Example Flow

터미널을 두 개 열고 진행합니다.

### Terminal 1: Receiver

```bash
source .venv/bin/activate
python listening_python_can.py
```

실행하면 다음처럼 수신 대기 상태가 됩니다.

```text
Listening on vcan0
Press Ctrl+C to stop.

+--------+------------+-----+-------------------------+
| CH     | CAN ID     | DLC | DATA                    |
+--------+------------+-----+-------------------------+
```

### Terminal 2: Sender

```bash
source .venv/bin/activate
python send_python_can.py
```

프롬프트가 나오면 CAN ID와 데이터를 입력합니다.

```text
vcan0> 7E0 02 10 01 AA AA AA AA AA
```

송신기에는 다음처럼 출력됩니다.

```text
Sent 7E0 [8] 02 10 01 AA AA AA AA AA
```

수신기에는 다음처럼 출력됩니다.

```text
| vcan0  | 0x000007E0 |   8 | 02 10 01 AA AA AA AA AA |
```

송신기를 종료하려면 다음 중 하나를 입력합니다.

```text
q
quit
exit
```

수신기를 종료하려면 `Ctrl+C`를 누릅니다.

<br />

## Cleanup

실험이 끝난 뒤 `vcan0` 인터페이스를 삭제하려면 다음 명령을 실행합니다.

```bash
./del_vcan.sh
```

<br />
<br />

# Files

### **Files**

```text
.
├── requirements.txt
├── pyproject.toml
├── setup_vcan.sh
├── del_vcan.sh
├── listening_python_can.py
├── send_python_can.py
├── src
│   ├── frame.py
│   ├── candump_parser.py
│   └── python_can_parser.py
└── tests
    ├── test_candump_parser.py
    └── test_python_can_parser.py
```

<br />

## Root Files

- `requirements.txt`
  - `python-can`, `pytest` 등 실행과 테스트에 필요한 Python 패키지를 고정합니다.

- `pyproject.toml`
  - pytest 설정을 담고 있습니다.
  - `pythonpath = [".", "src"]` 설정으로 테스트에서 root 예제와 `src` 모듈을 바로 import합니다.

- `setup_vcan.sh`
  - Linux 커널의 `vcan` 모듈을 로드합니다.
  - `vcan0` 인터페이스가 없으면 생성합니다.
  - 생성한 `vcan0` 인터페이스를 `up` 상태로 올립니다.

- `del_vcan.sh`
  - `vcan0` 인터페이스가 있으면 삭제합니다.

- `listening_python_can.py`
  - `python-can`으로 `vcan0`에서 CAN 메시지를 계속 수신합니다.
  - 수신한 `can.Message`를 `parse_python_can_message()`로 `CANFrame` 형태로 변환합니다.
  - 결과를 표 형태로 출력합니다.

- `send_python_can.py`
  - 사용자 입력을 기다리다가 입력된 CAN ID와 data byte를 `vcan0`으로 전송합니다.
  - 입력 형식은 `<can_id_hex> <data_hex...>`입니다.
  - 예: `7E0 02 10 01 AA AA AA AA AA`
  - `q`, `quit`, `exit` 중 하나를 입력하면 종료합니다.

<br />

## Source Files

- `src/frame.py`
  - 프로젝트 내부에서 공통으로 사용하는 `CANFrame` dataclass를 정의합니다.
  - 필드는 `channel`, `can_id`, `dlc`, `data`입니다.

- `src/candump_parser.py`
  - `candump` 출력 한 줄을 `CANFrame`으로 변환합니다.
  - 예: `can0 7E8 [8] 02 10 01 AA AA AA AA AA`

- `src/python_can_parser.py`
  - `python-can`의 `can.Message` 객체를 `CANFrame`으로 변환합니다.
  - `listening_python_can.py`에서 수신한 메시지를 표준 형태로 바꿀 때 사용합니다.

<br />

## Test Files

- `tests/test_candump_parser.py`
  - `candump` 텍스트 한 줄이 `CANFrame`으로 올바르게 파싱되는지 확인합니다.

- `tests/test_python_can_parser.py`
  - `python-can`의 `can.Message` 객체가 `CANFrame`으로 올바르게 변환되는지 확인합니다.
