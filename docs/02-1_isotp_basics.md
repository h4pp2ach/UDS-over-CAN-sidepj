# Step 2-1. ISO-TP Basics

이 문서는 현재 프로젝트의 두 번째 단계인 ISO-TP frame parser, payload reassembler, 송신용 segmenter와 `vcan0` 기반 송수신 예제를 정리합니다.

현재 단계의 목적은 CAN data bytes 위에서 ISO-TP 전송 프레임 구조를 다뤄보는 것입니다.

- CAN data bytes를 ISO-TP frame 타입으로 파싱
- Single Frame payload 처리
- First Frame + Consecutive Frame payload 재조립
- 송신용 ISO-TP segment 구성
- `vcan0` 기반 실제 송수신 흐름을 눈으로 확인

<br />

## ISO-TP Frame Types

현재 구현은 Classical CAN 기준의 기본 ISO-TP frame을 다룹니다.

```text
Single Frame       SF  payload가 한 CAN frame에 들어가는 경우
First Frame        FF  multi-frame payload의 시작
Consecutive Frame  CF  FF 뒤에 이어지는 payload 조각
Flow Control       FC  수신 측이 송신 속도/진행 여부를 제어하는 frame
```

PCI byte의 상위 4비트는 frame type이고, 하위 4비트는 frame type에 따라 다르게 해석합니다.

```text
0x0n  Single Frame       n = payload length
0x1n  First Frame        n + next byte = total payload length
0x2n  Consecutive Frame  n = sequence number
0x3n  Flow Control       n = flow status
```

<br />

## Environment Setup

이 단계는 Step 1과 같은 Linux SocketCAN 환경을 사용합니다. `vcan0` 인터페이스와 Python 가상환경이 준비되어 있어야 합니다.

참고: [Step 1. CAN Frame Parser](01_can_frame_parser.md)

<br />

## ISO-TP Roles

현재 단계의 코드는 ISO-TP 처리 흐름을 세 부분으로 나눕니다.

```text
isotp_frame                 : CAN data bytes -> ISO-TP frame 객체
isotp_payload_reassembler   : ISO-TP frame 객체들 -> 완성된 payload bytes
isotp_tx_segmenter          : 송신 payload bytes -> ISO-TP CAN data bytes 목록
```

`listening_isotp.py`는 `python-can`으로 `vcan0`에서 직접 메시지를 받고, `isotp_frame.py`와 `isotp_payload_reassembler.py`를 연결해 수신 payload를 확인합니다.

`send_isotp_scenario.py`는 사용자가 고른 시나리오 payload를 `isotp_tx_segmenter.py`로 나눈 뒤, 각 조각을 `can.Message`로 감싸 `vcan0`에 전송합니다.

### `src/isotp_frame.py`

`isotp_frame.py`의 책임은 **한 개의 CAN data bytes를 한 개의 ISO-TP frame 객체로 파싱하는 것**입니다.

담당하는 일:

- `SingleFrame`, `FirstFrame`, `ConsecutiveFrame`, `FlowControlFrame` dataclass 정의
- `FlowStatus` enum 정의
- 첫 PCI byte를 보고 frame type 판별
- frame type별 필드 추출
- frame 자체가 성립하지 않는 경우 `ValueError` 발생

즉 이 파일은 stateless parser입니다. 같은 입력에 대해 같은 frame 객체를 반환하고, 이전/다음 frame 상태를 기억하지 않습니다.

### `src/isotp_payload_reassembler.py`

`isotp_payload_reassembler.py`의 책임은 **파싱된 ISO-TP frame 객체들을 받아 최종 payload bytes로 재조립하는 것**입니다.

담당하는 일:

- `SingleFrame`이면 payload를 즉시 반환
- `FirstFrame`이면 조립 상태를 시작하고 `None` 반환
- `ConsecutiveFrame`이면 sequence number를 확인하고 payload 누적
- 전체 길이에 도달하면 완성된 payload 반환
- 마지막 CF의 padding은 `total_length` 기준으로 잘라냄
- 잘못된 frame 순서나 오염된 조립 상태를 예외로 처리하고 reset

현재 `listening_isotp.py`는 `(channel, can_id)`별로 `IsoTpPayloadReassembler`를 따로 들고 있습니다. 이 분리는 reassembler가 순수하게 payload 조립만 책임지게 하려는 의도입니다.

### `src/isotp_tx_segmenter.py`

`isotp_tx_segmenter.py`의 책임은 **송신할 UDS/진단 payload bytes를 ISO-TP CAN data bytes 목록으로 나누는 것**입니다.

담당하는 일:

- payload 길이에 따라 SF 또는 FF/CF 구성 선택
- SF PCI byte 생성
- FF total length encoding
- CF sequence number 생성
- ISO-TP 최대 payload 길이 검증


즉 이 파일은 송신 방향의 순수 segmentation 로직입니다. `send_isotp_scenario.py`에서 이 결과를 `can.Message`로 감싸서 `vcan0`에 보내게 됩니다.

<br />

## Test

가상환경을 활성화한 뒤 pytest를 실행합니다.

```bash
source .venv/bin/activate
python -m pytest tests/test_isotp_frame.py tests/test_isotp_payload_reassembler.py tests/test_send_isotp_segmenter.py
```

전체 테스트를 실행하려면 다음 명령을 사용합니다.

```bash
python -m pytest
```

현재 테스트는 다음 내용을 확인합니다.

- CAN data bytes를 ISO-TP frame 객체로 변환
- 잘못된 ISO-TP frame 형식 예외 처리
- Single Frame payload 즉시 반환
- First Frame + Consecutive Frame payload 재조립
- 잘못된 CF sequence number 예외 처리
- 조립 중 새 SF/FF 수신 예외 처리
- Flow Control frame 파싱
- 송신 payload를 SF/FF/CF data bytes로 분할
- ISO-TP 시나리오 선택과 잘못된 입력 예외 처리

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

현재 `send_isotp_scenario.py`와 `listening_isotp.py`는 ISO-TP 기본 동작을 눈으로 확인할 수 있도록 구성되어 있습니다.

주의: 이 예제는 FF 이후 Flow Control을 기다렸다가 CF를 보내는 완전한 ISO-TP 송신 상태 머신은 아닙니다. 현재는 payload를 ISO-TP frame으로 나눠 보내고, 수신 측에서 파싱/재조립 결과를 확인하는 용도입니다.

sender는 frame 사이에 `FRAME_DELAY_SECONDS`만큼 delay를 넣어 CF가 순서대로 들어오는 모습을 확인하기 쉽게 합니다.

### Terminal 1: Receiver

```bash
source .venv/bin/activate
python listening_isotp.py
```

실행하면 다음처럼 수신 대기 상태가 됩니다.

```text
Listening ISO-TP on vcan0
Press Ctrl+C to stop.
```

### Terminal 2: Sender

```bash
source .venv/bin/activate
python send_isotp_scenario.py
```

메뉴에서 원하는 시나리오 번호를 선택합니다.

```text
ISO-TP scenarios
1. Single Frame - Diagnostic Session Control
2. Multi Frame - short TransferData payload (FF + CF 1)
3. Multi Frame - medium TransferData payload (FF + CF 2)
4. Multi Frame - long TransferData payload (FF + CF 4)
q. quit
vcan0 scenario> 4
```

송신기에는 다음처럼 출력됩니다.

```text
Scenario: Multi Frame - long TransferData payload (FF + CF 4)
Payload: 36 03 46 57 42 4C 4B 30 33 5F 41 44 44 52 30 30 30 30 33 30 30 30 5F 53 49 5A 45 30 30 33 32 5F 4F 4B
Sent [1/5] id = 0x00000200  dlc = 8  data = 10 22 36 03 46 57 42 4C
Sent [2/5] id = 0x00000200  dlc = 8  data = 21 4B 30 33 5F 41 44 44
Sent [3/5] id = 0x00000200  dlc = 8  data = 22 52 30 30 30 30 33 30
Sent [4/5] id = 0x00000200  dlc = 8  data = 23 30 30 5F 53 49 5A 45
Sent [5/5] id = 0x00000200  dlc = 8  data = 24 30 30 33 32 5F 4F 4B
```

수신기에는 다음처럼 출력됩니다.

```text
CAN   ch = vcan0  id = 0x00000200  dlc = 8  data = 10 22 36 03 46 57 42 4C
ISO-TP   [1/5]  type = FF  total = 34  initial = 36 03 46 57 42 4C
CAN   ch = vcan0  id = 0x00000200  dlc = 8  data = 21 4B 30 33 5F 41 44 44
ISO-TP   [2/5]  type = CF  sn = 1  chunk = 4B 30 33 5F 41 44 44
...
CAN   ch = vcan0  id = 0x00000200  dlc = 8  data = 24 30 30 33 32 5F 4F 4B
ISO-TP   [5/5]  type = CF  sn = 4  chunk = 30 30 33 32 5F 4F 4B
DONE     payload = 36 03 46 57 42 4C 4B 30 33 5F 41 44 44 52 30 30 30 30 33 30 30 30 5F 53 49 5A 45 30 30 33 32 5F 4F 4B
------------------------------------------------------------------------------------------------
```

raw CAN frame을 직접 보내고 싶으면 Step 1의 `send_python_can.py`를 사용합니다.

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

# Note

### **Transport Layer 반영 상태**

아래 항목은 후속 구현인 [Step 2-2. ISO-TP Transport Layer](02-2_isotp_transport_layer.md)에서 구현하고 테스트했습니다.

Transport Layer에 반영된 항목:

- FF 수신 후 Flow Control Frame 생성
- 송신 측이 Flow Control을 기다린 뒤 CF 전송
- block size 처리
- STmin 지연 처리
- request/response CAN ID 관리
- timeout 정책 추가

따라서 다음 개발 단계는 Transport Layer 위에서 UDS request/response와 diagnostic service를 처리하는 Step 3입니다.

<br />

## Design Choices

<details close>
<summary><strong>1. dataclass 기반 frame 객체를 선택한 이유</strong></summary>
<br />
선택한 방식:

```text
bytes -> parse_isotp_frame() -> SingleFrame / FirstFrame / ConsecutiveFrame / FlowControlFrame
```

장점:
- 테스트에서 frame type과 필드를 명확히 검증할 수 있음
- reassembler가 raw byte parsing을 몰라도 됨
- UDS 단계에서 payload만 다루기 쉬움
- frame별 필드 이름이 명시적이라 코드 읽기가 쉬움

단점:
- 작은 코드에 class가 여러 개 생김
- 단순 tuple이나 dict보다 파일 길이가 조금 늘어남

대안:
- `dict`로 반환: 빠르게 만들 수 있지만 key 오타와 타입 혼동이 생기기 쉬움
- tuple로 반환: 가장 짧지만 `frame[0]`, `frame[1]` 식 코드가 늘어나 의미가 흐려짐
- raw bytes만 유지: 처음엔 단순하지만 reassembler와 UDS parser가 모두 PCI bit 연산을 반복하게 됨

현재 단계에서는 학습과 테스트 명확성이 중요해서 dataclass 방식을 선택.
</details>

<br />

<details close>
<summary><strong>2. parser와 reassembler를 분리한 이유</strong></summary>
<br />
선택한 방식:

```text
CAN data bytes
    ↓
isotp_frame.parse_isotp_frame()
    ↓
IsoTpPayloadReassembler.feed()
    ↓
완성된 payload bytes
```

장점:

- frame parsing과 multi-frame 상태 관리가 섞이지 않음
- `isotp_frame.py`는 stateless라 테스트가 단순함
- `isotp_payload_reassembler.py`는 sequence/order 검증에만 집중할 수 있음
- 나중에 송신기, 수신기, Virtual ECU가 같은 parser를 재사용할 수 있음

단점:

- 호출자가 두 단계를 연결해야 함
- `listening_isotp.py`처럼 CAN ID별 reassembler map을 별도로 관리해야 함

대안:

- 하나의 ISO-TP class가 raw CAN frame부터 payload까지 모두 처리
  - 장점: 호출부는 단순함
  - 단점: parsing, state, CAN ID routing, Flow Control 정책이 한곳에 섞이기 쉬움

현재는 각 단계의 책임을 눈으로 확인하고 테스트하기 위해 분리.
</details>

<br />

<details close>
<summary><strong>3. `feed()`가 `bytes | None`을 반환하는 이유</strong></summary>
<br />
선택한 방식:

```text
bytes  완성된 payload
None   아직 조립 중
```

장점:

- API가 짧고 사용하기 쉬움
- Single Frame과 multi-frame 완료를 같은 방식으로 받을 수 있음
- 현재 테스트와 데모 코드에 충분함

단점:

- 나중에 Flow Control 송신 필요 여부, 진행률, block size 상태 등을 표현하기에는 부족함

대안:

- callback 방식: 완료 시 함수를 호출하게 할 수 있지만 테스트와 흐름 추적이 복잡해짐
- result object 방식: `ReassemblyResult(done=True, payload=...)`처럼 확장성이 좋지만 현재 단계에는 과함
- exception 없는 status code 방식: C 스타일에 가깝고 Python 코드에서는 오류 처리가 흐려질 수 있음

현재는 최소 API가 더 적합하므로 `bytes | None`을 선택.
</details>

<br />

<details close>
<summary><strong>4. TX segmenter가 `can.Message`를 만들지 않는 이유</strong></summary>
<br />
선택한 방식:

```text
payload bytes -> segment_isotp_payload() -> list[bytes]
```

장점:

- ISO-TP segmentation 로직이 `python-can`에 의존하지 않음
- bus 없이 단위 테스트가 쉬움
- 나중에 Virtual ECU나 transport layer에서도 같은 segmentation을 재사용할 수 있음
- CAN ID, channel, socketcan 설정 같은 I/O 책임이 sender에 남음

단점:

- sender에서 `bytes`를 `can.Message`로 감싸는 코드가 필요함

대안:

- `segment_isotp_payload(can_id, payload) -> list[can.Message]`
  - 장점: sender 코드는 더 짧아짐
  - 단점: 핵심 ISO-TP 로직이 `python-can`에 묶임

현재는 ISO-TP 핵심 로직을 재사용 가능한 형태로 유지하기 위해 `list[bytes]` 반환 방식을 선택.
</details>

<br />

<details close>
<summary><strong>5. 조립 중 새 SF/FF가 들어오면 예외를 던지는 이유</strong></summary>
<br />
선택한 방식: 조립 중 `SingleFrame`이나 새 `FirstFrame`이 들어오면 현재 구현은 상태를 reset하고 `ValueError`를 던집니다.

<br />

장점:

- CF 누락, CAN ID 섞임, 송신 순서 오류를 조용히 숨기지 않음
- 테스트에서 잘못된 흐름을 명확하게 잡을 수 있음
- 상위 transport layer가 실패를 인지하고 재시도/중단 정책을 세울 수 있음

단점:

- 실제 bus에는 여러 ECU/클라이언트 frame이 섞일 수 있으므로, 단일 reassembler만 쓰면 예외가 자주 날 수 있음

이 문제는 reassembler 내부에서 해결하지 않고, 호출부가 `(channel, can_id)`별 reassembler를 따로 두는 방식으로 해결합니다. 현재 `listening_isotp.py`가 이 방식을 사용합니다.

대안:
- 에러 raise 없이 조용히 reset하고 새 frame을 처리
  - 장점: 데모에서는 부드럽게 보임
  - 단점: 데이터 유실이나 순서 오류를 감춤
- 새 FF만 허용하고 SF는 허용하지 않음히
  - 장점: 일부 실제 구현과 비슷하게 동작 가능
  - 단점: 규칙이 애매해지고 테스트 정책이 복잡해짐

현재는 오류를 빨리 드러내는 편이 더 안전해서 예외를 선택.
</details>

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
├── listening_isotp.py
├── send_isotp_scenario.py
├── src
│   ├── isotp_frame.py
│   ├── isotp_payload_reassembler.py
│   └── isotp_tx_segmenter.py
└── tests
    ├── test_isotp_frame.py
    ├── test_isotp_payload_reassembler.py
    └── test_send_isotp_segmenter.py
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

- `listening_isotp.py`
  - `python-can`으로 `vcan0`에서 CAN 메시지를 계속 수신합니다.
  - 수신한 `can.Message`를 `parse_python_can_message()`로 `CANFrame` 형태로 변환합니다.
  - CAN data bytes를 `parse_isotp_frame()`으로 ISO-TP frame 객체로 변환합니다.
  - `(channel, can_id)`별 reassembler를 사용해 multi-frame payload를 재조립합니다.
  - CAN frame, ISO-TP frame, 최종 payload, 예외 로그를 한 줄 단위로 출력합니다.

- `send_isotp_scenario.py`
  - 사용자에게 ISO-TP 송신 시나리오 메뉴를 보여줍니다.
  - 선택된 payload를 `segment_isotp_payload()`로 ISO-TP data bytes 목록으로 나눕니다.
  - 각 ISO-TP data bytes를 `can.Message`로 감싸 `vcan0`에 순서대로 전송합니다.
  - frame 사이에 delay를 넣어 listener에서 수신 흐름을 확인하기 쉽게 합니다.

<br />

## Source Files

- `src/isotp_frame.py`
  - ISO-TP frame dataclass와 `FlowStatus` enum을 정의합니다.
  - CAN data bytes 한 개를 ISO-TP frame 객체로 파싱합니다.

- `src/isotp_payload_reassembler.py`
  - `SingleFrame`, `FirstFrame`, `ConsecutiveFrame`을 받아 payload를 재조립합니다.
  - 잘못된 frame 순서와 sequence number를 예외로 처리합니다.

- `src/isotp_tx_segmenter.py`
  - 송신 payload bytes를 ISO-TP SF 또는 FF/CF data bytes 목록으로 분할합니다.
  - `python-can`에 의존하지 않는 순수 송신 segmentation 로직입니다.

<br />

## Test Files

- `tests/test_isotp_frame.py`
  - ISO-TP frame type별 parsing과 잘못된 frame 형식 예외 처리를 확인합니다.

- `tests/test_isotp_payload_reassembler.py`
  - Single Frame 반환, multi-frame 재조립, sequence number 오류, 조립 중 새 frame 수신 오류를 확인합니다.

- `tests/test_send_isotp_segmenter.py`
  - 송신 payload segmentation, 시나리오 frame 수, 잘못된 payload와 시나리오 입력 예외 처리를 확인합니다.
