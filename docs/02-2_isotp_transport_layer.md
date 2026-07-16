# Step 2-2. ISO-TP Transport Layer

이 문서는 현재 프로젝트에 추가된 `isotp_transport_layer`와 interactive flow demo를 정리합니다.

이전 단계인 Step 2-1의 `02-1_isotp_basics.md`에서는 ISO-TP frame parser, payload reassembler, tx segmenter를 각각 따로 구현했습니다. 이번 단계의 목적은 그 조각들을 하나의 transport layer API로 묶고, 실제 ISO-TP 송수신에서 필요한 Flow Control 흐름을 코드로 확인하는 것입니다.

현재 단계에서 새로 다루는 핵심은 다음과 같습니다.

- `send(payload)`가 multi-frame 전송 시 First Frame 전송 후 Flow Control 대기
- Flow Control의 `CTS`, `WAIT`, `OVERFLOW` 상태 처리
- `BS(Block Size)`에 따른 block 단위 CF 전송
- `STmin`에 따른 Consecutive Frame 간 지연 적용
- `recv()`가 First Frame 수신 후 Flow Control 응답
- FC/BS/STmin 흐름을 볼 수 있는 interactive demo 제공

<br />

## Position

현재 프로젝트의 ISO-TP 구현은 다음 순서로 확장되고 있습니다.

```text
Step 2-1. ISO-TP Basics
    ├── isotp_frame.py
    ├── isotp_payload_reassembler.py
    └── isotp_tx_segmenter.py
            ↓
Step 2-2. ISO-TP Transport Layer
    └── isotp_transport_layer.py
```

Step 2-1의 기존 파일들은 ISO-TP frame 자체를 파싱하고 조립하는 순수 로직입니다. `IsoTpTransportLayer`는 그 로직 위에 CAN bus 송수신, Flow Control, 주소 방향, timeout, padding을 얹은 계층입니다.

```text
UDS payload bytes
    ↓
IsoTpTransportLayer.send()
    ↓
isotp_tx_segmenter.segment_isotp_payload()
    ↓
python-can can.Message
    ↓
bus.send()

bus.recv()
    ↓
python-can can.Message
    ↓
isotp_frame.parse_isotp_frame()
    ↓
IsoTpPayloadReassembler.feed()
    ↓
IsoTpTransportLayer.recv()
    ↓
UDS payload bytes
```

<br />

## Scope

현재 구현은 Classical CAN 기반의 기본 ISO-TP normal addressing 흐름을 학습용으로 구현한 것입니다.

현재 단계에서 포함하는 것:

- Classical CAN data length 범위의 ISO-TP segmentation
- 12-bit ISO-TP payload length, 최대 4095 bytes
- Single Frame 송수신
- First Frame + Consecutive Frame 송수신
- Flow Control Frame 생성과 파싱
- `CONTINUE_TO_SEND`, `WAIT`, `OVERFLOW` 처리
- `block_size`, `st_min`, `padding_byte`, `frame_timeout_seconds` 설정
- 11-bit standard CAN ID와 29-bit extended CAN ID


포함하지 않는 것:

- UDS service parsing
- 실제 ECU 상태 머신
- CAN FD 전용 ISO-TP 확장
- ISO-TP extended addressing, mixed addressing
- functional addressing 정책
- 비동기 I/O 기반 transport
- 같은 bus를 여러 transport가 공유하는 동시 수신과 message 보존

현재 데모와 테스트는 하나의 `bus`를 하나의 `IsoTpTransportLayer`가 독점해서 사용하는 구조입니다. 별도 message router 없이 transport가 `bus.send()`와 `bus.recv()`를 직접 호출합니다. 송신 중 FC를 기다릴 때와 `recv()` 중에는 설정된 `rx_can_id`와 `is_extended_id`가 일치하지 않는 frame을 보관하지 않고 버리며, 이 과정에서도 한 번 정한 frame timeout deadline은 늘리지 않습니다.

<br />

## Environment Setup

기본 환경은 이전 문서와 같습니다.

참고:

- [Step 1. CAN Frame Parser](01_can_frame_parser.md)
- [Step 2-1. ISO-TP Basics](02-1_isotp_basics.md)

Python 가상환경을 실행합니다.

이번 interactive flow demo는 실제 `vcan0` 인터페이스 없이도 실행됩니다. `InteractiveFlowControlBus`가 sender/receiver 사이의 bus 역할을 한 프로세스 안에서 흉내냅니다.

다만 실제 SocketCAN 기반 송수신을 확인하려면 기존과 같이 `vcan0`을 준비해야 합니다.

```bash
./setup_vcan.sh
```

<br />

## Transport Layer API

### `IsoTpTransportLayer.for_client()`

client 입장에서 transport를 생성합니다.

```python
transport = IsoTpTransportLayer.for_client(
    bus,
    request_can_id=0x7E0,
    response_can_id=0x7E8,
)
```

client 기준으로는 다음 방향이 됩니다.

```text
tx_can_id = request_can_id
rx_can_id = response_can_id
```

즉 client는 `0x7E0`으로 request를 보내고, `0x7E8`에서 response를 받습니다.

### `IsoTpTransportLayer.for_server()`

server 입장에서 transport를 생성합니다.

```python
transport = IsoTpTransportLayer.for_server(
    bus,
    request_can_id=0x7E0,
    response_can_id=0x7E8,
)
```

server 기준으로는 다음 방향이 됩니다.

```text
tx_can_id = response_can_id
rx_can_id = request_can_id
```

즉 server는 `0x7E0`으로 들어온 request를 받고, `0x7E8`으로 response를 보냅니다.

### CAN ID 형식

기본값인 `is_extended_id=False`는 11-bit standard CAN ID를 사용합니다. 29-bit extended CAN ID를 사용하려면 client/server factory에 `is_extended_id=True`를 전달합니다.

```python
transport = IsoTpTransportLayer.for_client(
    bus,
    request_can_id=0x18DA10F1,
    response_can_id=0x18DAF110,
    is_extended_id=True,
)
```

이 설정은 CAN ID 형식을 구분하며 ISO-TP extended addressing을 의미하지 않습니다. 현재 구현은 송신 CAN ID와 수신 CAN ID가 같으면 잘못된 설정으로 처리합니다.

### 주요 설정값

```python
IsoTpTransportLayer(
    bus,
    address,
    block_size=0,
    st_min=0,
    frame_timeout_seconds=1.0,
    max_wait_frame_count=8,
    tx_data_length=8,
    padding_byte=None,
)
```

- `block_size`
  - 수신자가 한 번의 FC로 허용할 CF 개수입니다.
  - `0`이면 남은 CF 전체를 허용합니다.
  - `1..255`이면 해당 개수만큼 CF를 받은 뒤 추가 FC를 보냅니다.

- `st_min`
  - 수신자가 요청하는 Consecutive Frame 사이의 최소 간격입니다.
  - `0x00..0x7F`는 millisecond 단위입니다.
  - `0xF1..0xF9`는 100 microsecond 단위입니다.

- `frame_timeout_seconds`
  - 다음 ISO-TP CAN frame을 기다리는 timeout입니다.
  - 현재 구현에서는 전체 payload timeout이 아니라 frame 단위 timeout으로 사용합니다.

- `max_wait_frame_count`
  - sender가 허용할 Flow Control `WAIT` frame 최대 개수입니다.
  - 초과하면 `IsoTpFlowControlError`를 발생시킵니다.

- `tx_data_length`
  - 한 CAN frame의 data length입니다.
  - 현재 Classical CAN 기준으로 `3..8` 범위를 허용합니다.

- `padding_byte`
  - `None`이면 padding하지 않습니다.
  - `0x00..0xFF` 값을 주면 송신 CAN data를 `tx_data_length`까지 채웁니다.

`recv(timeout_seconds=...)`를 호출하면 해당 `recv()`에서만 `frame_timeout_seconds`를 대신할 frame timeout을 사용할 수 있습니다.

<br />

## Send Flow

`IsoTpTransportLayer.send(payload)`는 payload 길이에 따라 Single Frame 또는 multi-frame 전송을 수행합니다.

### Single Frame

payload가 한 CAN frame에 들어가면 FC를 기다리지 않습니다.

```text
payload
    ↓
segment_isotp_payload()
    ↓
SF data
    ↓
bus.send()
```

예:

```python
transport.send(bytes.fromhex("10 03"))
```

송신 data:

```text
02 10 03
```

`padding_byte=0xAA`라면 다음처럼 `tx_data_length=8`까지 padding됩니다.

```text
02 10 03 AA AA AA AA AA
```

### Multi Frame

payload가 한 CAN frame에 들어가지 않으면 다음 흐름을 따릅니다.

```text
payload
    ↓
segment_isotp_payload()
    ↓
FF 송신
    ↓
FC 수신 대기
    ↓
CTS면 CF 송신 시작
    ↓
STmin 적용
    ↓
BS만큼 보낸 뒤 필요하면 다음 FC 대기
    ↓
모든 CF 송신 완료
```

예:

```python
transport.send(bytes.fromhex("36 01 AA BB CC DD EE FF 00 11 22"))
```

송신 payload 길이는 11 bytes입니다. `tx_data_length=8`이면 다음처럼 분할됩니다.

```text
FF  10 0B 36 01 AA BB CC DD
CF  21 EE FF 00 11 22
```

sender는 FF를 보낸 뒤 receiver의 FC를 기다립니다.

```text
SENDER -> RECEIVER  10 0B 36 01 AA BB CC DD
SENDER waits for FC
```

receiver가 다음 FC를 보내면 sender는 CF를 보냅니다.

```text
RECEIVER -> SENDER  30 00 00
SENDER   -> RECEIVER  21 EE FF 00 11 22
```

FC byte 의미:

```text
30 00 00
│  │  └── STmin = 0 ms
│  └───── BS = 0, 남은 CF 전체 허용
└──────── Flow Status = 0, Continue To Send
```

<br />

## Receive Flow

`IsoTpTransportLayer.recv()`는 `address.rx_can_id`에 해당하는 CAN message를 기다리고, ISO-TP payload가 완성되면 `bytes`를 반환합니다.

### Single Frame

Single Frame은 추가 Flow Control 없이 바로 payload를 반환합니다.

```text
bus.recv()
    ↓
SF 수신
    ↓
parse_isotp_frame()
    ↓
IsoTpPayloadReassembler.feed()
    ↓
payload 반환
```

예:

```text
CAN data = 02 62 00
payload  = 62 00
```

### Multi Frame

First Frame을 받으면 transport layer는 즉시 Flow Control Frame을 송신합니다.

```text
bus.recv()
    ↓
FF 수신
    ↓
reassembler 상태 시작
    ↓
FC 송신
    ↓
CF 수신 반복
    ↓
payload 완성 시 반환
```

예:

```text
RECEIVER <- SENDER  10 0B 62 F1 90 AA BB CC
RECEIVER -> SENDER  30 00 05
RECEIVER <- SENDER  21 DD EE FF 00 11
```

완성 payload:

```text
62 F1 90 AA BB CC DD EE FF 00 11
```

`block_size > 0`이면 receiver는 설정된 개수만큼 CF를 받은 뒤 추가 FC를 보냅니다.

```text
block_size = 2

FF 수신
FC 송신
CF #1 수신
CF #2 수신
FC 송신
CF #3 수신
CF #4 수신
...
```

마지막 CF를 받고 payload가 완성되면 추가 FC를 보내지 않고 반환합니다.

<br />

## Flow Control

Flow Control Frame은 receiver가 sender에게 전송 진행 여부와 속도를 알려주는 ISO-TP frame입니다.

현재 helper는 다음 함수입니다.

```python
build_flow_control_data(
    FlowStatus.CONTINUE_TO_SEND,
    block_size=8,
    st_min=0x0A,
)
```

반환 data:

```text
30 08 0A
```

구조:

```text
0x30 | FlowStatus
block_size
st_min
```

현재 지원하는 Flow Status:

```text
0  CONTINUE_TO_SEND
1  WAIT
2  OVERFLOW
```

### `CONTINUE_TO_SEND`

sender가 CF를 계속 보낼 수 있다는 의미입니다.

```text
30 03 32
│  │  └── STmin = 0x32 = 50 ms
│  └───── BS = 3
└──────── CTS
```

sender는 CF 3개를 보낸 뒤, 아직 남은 CF가 있으면 다음 FC를 기다립니다.

### `WAIT`

receiver가 아직 CF를 받을 준비가 되지 않았다는 의미입니다.

```text
31 00 00
```

sender는 CF를 보내지 않고 FC를 다시 기다립니다. 현재 구현은 무한 대기를 막기 위해 `max_wait_frame_count`를 둡니다.

### `OVERFLOW`

receiver가 더 받을 수 없다는 의미입니다.

```text
32 00 00
```

sender는 전송을 중단하고 `IsoTpFlowControlError`를 발생시킵니다.

<br />

## STmin

`STmin`은 Consecutive Frame 사이의 최소 간격입니다.

현재 decoding helper:

```python
decode_st_min_seconds(st_min)
```

지원 범위:

```text
0x00..0x7F  millisecond 단위
0xF1..0xF9  100 microsecond 단위
```

예:

```text
0x00 -> 0.0000 seconds
0x05 -> 0.0050 seconds
0x7F -> 0.1270 seconds
0xF1 -> 0.0001 seconds
0xF9 -> 0.0009 seconds
```

`0x80..0xF0`, `0xFA..0xFF`는 현재 구현에서 유효하지 않은 STmin으로 처리합니다.

현재 `send()`는 첫 CF를 보내기 전에는 sleep하지 않고, CF를 하나 이상 보낸 뒤 다음 CF부터 STmin 지연을 적용합니다.

```text
FF 송신
FC 수신
CF #1 송신
sleep(STmin)
CF #2 송신
sleep(STmin)
CF #3 송신
...
```

<br />

## Error Boundary

Transport가 사용하는 공통 예외 계층과 설계 근거는 [Step 2-1의 Design Choices](02-1_isotp_basics.md#design-choices)에서 설명합니다.

<br />

## Interactive Flow Demo

`isotp_transport_flow_demo.py`는 실제 CAN interface 없이 Flow Control 흐름을 확인하기 위한 demo입니다.

실행:

```bash
source .venv/bin/activate
python isotp_transport_flow_demo.py
```

demo 설정:

```text
request CAN ID   0x000007E0  sender(client) TX
response CAN ID  0x000007E8  receiver(server) FC TX
tx_data_length   8
padding_byte     0x00
FC timeout       30s
```

demo payload는 50 bytes입니다.

```text
36 01 49 53 4F 54 50 5F 44 45 4D 4F 5F 4C 4F 4E
47 5F 50 41 59 4C 4F 41 44 5F 42 4C 4F 43 4B 5F
30 30 30 31 5F 41 42 43 44 45 46 47 48 49 4A 4B
4C 4D
```

`tx_data_length=8`이므로 전송은 다음 구조가 됩니다.

```text
FF 1개
CF 7개
```

첫 frame:

```text
10 32 36 01 49 53 4F 54
│  │  └────────────────── initial payload 6 bytes
│  └───────────────────── total length = 0x32 = 50
└──────────────────────── First Frame
```

### Demo 흐름

실행하면 먼저 payload와 설정을 보여준 뒤, sender가 First Frame을 보냅니다.

```text
상황 #0 - First Frame
[    0.000 ms] (+   0.000 ms) SENDER  TX
  - CAN     id=0x000007E0  dlc=8
  - DATA
      10 32 36 01 49 53 4F 54

- 이벤트       : sender가 First Frame을 보냈습니다.
- 전체 payload : 50 bytes, (0x032 = 50)
- 이번 FF data : 6 bytes, 36 01 49 53 4F 54
- 남은 data    : 44 bytes
```

이후 sender는 receiver의 Flow Control을 기다립니다.

```text
FC #1 입력 - sender 대기 중
FC #1 상태 선택 (c/w/o/t/q)>
```

입력 가능한 상태:

```text
c  continue to send
w  wait
o  overflow
t  timeout 재현
q  quit
```

### 정상 완료 기준

`c`로 전송을 끝까지 진행해 CF 7개가 전송되고 다음 문구가 출력되면 정상입니다.

```text
전송 완료: FF 이후 FC 대기, STmin, BS 흐름을 확인해보세요.
```

<details close>
<summary><strong>예시 1. BS=0, STmin=0</strong></summary>
<br />

가장 단순한 진행입니다.

입력:

```text
FC #1 상태 선택 (c/w/o/t/q)> c
FC #1 BS 입력 (0..255)> 0
FC #1 STmin 입력(ms, 0..127)> 0
```

의미:

```text
BS = 0       남은 CF 전체 허용
STmin = 0    CF 사이 delay 없음
```

receiver가 보내는 FC:

```text
30 00 00 00 00 00 00 00
```

sender는 남은 CF 7개를 끝까지 보냅니다.

</details>

<br />

<details close>
<summary><strong>예시 2. BS=3, STmin=50</strong></summary>
<br />

block size와 STmin을 동시에 확인하는 진행입니다.

입력:

```text
FC #1 상태 선택 (c/w/o/t/q)> c
FC #1 BS 입력 (0..255)> 3
FC #1 STmin 입력(ms, 0..127)> 50
```

의미:

```text
BS = 3          CF 3개 전송 후 다음 FC 대기
STmin = 50 ms   CF 사이 최소 50 ms 간격
```

receiver가 보내는 FC:

```text
30 03 32 00 00 00 00 00
```

sender는 CF 3개를 보낸 뒤 다시 FC 입력을 기다립니다.

```text
SENDER TX  21 50 5F 44 45 4D 4F 5F
SENDER TX  22 4C 4F 4E 47 5F 50 41
SENDER TX  23 59 4C 4F 41 44 5F 42

FC #2 입력 - sender 대기 중
```

이때 demo log의 `+delta` 값을 보면 두 번째 CF부터 약 50 ms 간격이 반영되는 것을 확인할 수 있습니다.

남은 CF를 계속 보내려면 다시 `c`를 입력합니다.

```text
FC #2 상태 선택 (c/w/o/t/q)> c
FC #2 BS 입력 (0..255)> 0
FC #2 STmin 입력(ms, 0..127)> 0
```

이후 sender는 남은 CF를 끝까지 보냅니다.

</details>

<br />

<details close>
<summary><strong>예시 3. WAIT 후 CTS</strong></summary>
<br />

receiver가 아직 준비되지 않은 상황을 재현합니다.

입력:

```text
FC #1 상태 선택 (c/w/o/t/q)> w
```

receiver가 보내는 FC:

```text
31 00 00 00 00 00 00 00
```

sender는 CF를 보내지 않고 다음 FC를 계속 기다립니다.

```text
FC #2 상태 선택 (c/w/o/t/q)>
```

이후 `c`를 입력하면 전송이 진행됩니다.

</details>

<br />

<details close>
<summary><strong>예시 4. OVERFLOW</strong></summary>
<br />

receiver가 더 이상 받을 수 없는 상황을 재현합니다.

입력:

```text
FC #1 상태 선택 (c/w/o/t/q)> o
```

receiver가 보내는 FC:

```text
32 00 00 00 00 00 00 00
```

sender는 전송을 중단하고 다음 오류를 출력합니다.

```text
FLOW CONTROL ERROR: Receiver reported Flow Control overflow
```

</details>

<br />

<details close>
<summary><strong>예시 5. Timeout</strong></summary>
<br />

FC가 오지 않는 상황을 재현합니다.

입력:

```text
FC #1 상태 선택 (c/w/o/t/q)> t
```

demo bus는 `None`을 반환하고, transport layer는 timeout으로 처리합니다.

```text
TIMEOUT: Timed out waiting for ISO-TP CAN frame
```

</details>

<br />

## Design Choices

<details close>
<summary><strong>1. parser/reassembler/segmenter를 transport layer에서 조합</strong></summary>
<br />

구현하려는 것:

```text
UDS payload bytes를 send()/recv() 단위로 주고받는 ISO-TP transport API
```

구현한 방식:

```text
송신:
payload
    ↓
segment_isotp_payload()
    ↓
CAN data bytes
    ↓
can.Message
    ↓
bus.send()

수신:
bus.recv()
    ↓
can.Message.data
    ↓
parse_isotp_frame()
    ↓
IsoTpPayloadReassembler.feed()
    ↓
payload
```

왜 이렇게 했는가:

- Step 2-1에서 이미 frame parsing, payload reassembly, tx segmentation 책임이 분리되어 있음
- transport layer는 CAN I/O와 Flow Control에 집중할 수 있음
- 기존 단위 테스트가 있는 순수 로직을 재사용할 수 있음
- transport demo에서는 `bytes` payload만 다루면 되므로 호출부가 단순해짐

다른 구현 방법:

- transport layer 내부에서 PCI parsing, reassembly, segmentation을 모두 직접 처리
  - 장점: 파일 간 이동 없이 한 곳에서 전체 흐름을 볼 수 있음
  - 단점: parser, state machine, CAN I/O, error handling이 한 파일에 강하게 섞임

- parser/reassembler/segmenter를 class 하나로 합치기
  - 장점: public API 수가 줄어듦
  - 단점: 각 단계별 테스트가 흐려지고 학습용 구조가 덜 명확해짐

현재는 단계별 책임을 유지한 채 transport layer를 얹는 방식이 적절합니다.
</details>

<br />

<details close>
<summary><strong>2. 하나의 transport가 bus를 직접 사용하는 이유</strong></summary>
<br />

현재 MVP의 전제:

```text
하나의 bus
    ↓
하나의 IsoTpTransportLayer
    ↓
하나의 request/response 흐름
```

구현한 방식:

```text
bus.recv()
    ↓
CAN ID와 extended ID 여부 확인
    ↓
현재 transport 대상이면 처리
    ↓
대상이 아니면 버리고 같은 timeout deadline 안에서 계속 대기
```

왜 이렇게 했는가:

- 현재 interactive demo와 `vcan0` demo는 transport마다 독립된 bus를 사용함
- 여러 transport의 message를 보존하는 router는 ISO-TP frame 송수신 자체의 책임이 아님
- pending queue, TTL, 동시 수신 정책 없이도 현재 데모의 송수신 흐름을 확인할 수 있음
- transport가 bus를 직접 사용하면 FC 대기와 CAN ID 필터링 흐름이 단순해짐

현재 제약:

- 같은 bus를 여러 transport가 동시에 소비하는 흐름은 지원하지 않음
- 현재 transport 대상이 아닌 CAN frame은 별도로 보관하지 않음

현재 단계에서는 ISO-TP 핵심 동작에 집중하기 위해 이 구조를 사용합니다.
</details>

<br />

<details close>
<summary><strong>3. blocking `send()`/`recv()` API 선택</strong></summary>
<br />

구현하려는 것:

```text
현재 demo의 request/response 흐름을 순서대로 확인하기 쉬운 동기식 ISO-TP transport API
```

구현한 방식:

```python
transport.send(request_payload)
response_payload = transport.recv()
```

`send()`는 필요한 FC를 기다리며 CF를 순서대로 보냄. `recv()`는 payload가 완성될 때까지 CAN frame을 읽고, 필요한 시점에 FC를 보냄.

왜 이렇게 했는가:

- 테스트에서 event 순서를 검증하기 쉬움
- interactive demo에서 사용자가 FC를 입력하는 흐름과 잘 맞음
- 현재 단일 client/server 흐름에서는 async/event loop가 필요하지 않음

다른 구현 방법:

- callback 방식
  - 장점: frame 도착 시점마다 반응하기 좋음
  - 단점: 테스트와 demo 흐름이 복잡해짐
- async/await 방식
  - 장점: 여러 transport를 동시에 다루기 좋음
  - 단점: 프로젝트 전체가 async 구조를 고려해야 함

현재 demo와 단일 client/server 송수신에는 blocking API가 가장 단순합니다.
</details>

<br />

<details close>
<summary><strong>4. Flow Control 처리</strong></summary>
<br />

구현하려는 것:

```text
ISO-TP multi-frame 송수신에서 sender와 receiver가 서로 속도와 진행 여부를 조율하는 흐름
```

구현한 방식:

송신 방향:

```text
FF 송신
FC 수신 대기
CTS면 CF 송신
WAIT면 계속 대기
OVERFLOW면 중단
BS만큼 CF 송신 후 추가 FC 대기
```

수신 방향:

```text
FF 수신
FC 송신
CF 수신
BS만큼 받으면 추가 FC 송신
payload 완성 시 반환
```

왜 이렇게 했는가:

- Step 2-1의 sender는 FF 이후 FC를 기다리지 않고 CF를 바로 보냈음
- 실제 ISO-TP multi-frame에서는 receiver가 FC로 전송 가능 여부를 알려야 함
- `BS`와 `STmin`은 transport layer의 책임이지 UDS service parser의 책임이 아님
- FC를 처리해야 현재 multi-frame demo가 실제 ISO-TP 순서대로 동작함

다른 구현 방법:

- Flow Control 정책을 별도 strategy 객체로 분리
  - 장점: receiver 정책을 바꿔 끼우기 쉬움
  - 단점: 현재 단계에는 abstraction이 먼저 커짐

현재는 transport layer 내부 설정값으로 `block_size`, `st_min`, `max_wait_frame_count`를 받는 정도가 적절합니다.
</details>

<br />

<details close>
<summary><strong>5. padding을 segmenter가 아니라 transport layer에서 처리</strong></summary>
<br />

구현하려는 것:

```text
송신 CAN data를 필요할 때 tx_data_length까지 padding하는 기능
```

구현한 방식:

```text
segment_isotp_payload()
    ↓
실제 ISO-TP data bytes 생성
    ↓
IsoTpTransportLayer._pad_can_data()
    ↓
padding_byte가 있으면 tx_data_length까지 채움
```

왜 이렇게 했는가:

- segmenter의 책임은 payload를 ISO-TP data 조각으로 나누는 것임
- padding은 CAN 송신 policy에 가깝고 bus로 보내기 직전에 결정해도 됨
- `padding_byte=None`이면 DLC가 짧은 frame도 보낼 수 있음
- 같은 segmenter 결과를 padding이 필요한 demo와 필요 없는 테스트에서 모두 재사용할 수 있음

다른 구현 방법:

- segmenter가 항상 8 bytes frame을 반환
  - 장점: 송신부가 단순함
  - 단점: padding 정책이 순수 segmentation 로직에 섞임
- `segment_isotp_payload(payload, padding_byte=...)`로 segmenter에 padding 옵션 추가
  - 장점: frame list가 최종 CAN data와 같아짐
  - 단점: padding 없는 논리 frame과 padding된 CAN frame의 구분이 흐려짐

현재는 transport layer에서 padding을 적용해 책임을 분리합니다.
</details>

<br />
<br />

# Files

### **Files**

```text
.
├── isotp_transport_flow_demo.py
├── isotp_transport_demo_common.py
├── isotp_transport_vcan_sender_demo.py
├── isotp_transport_vcan_interactive_receiver.py
├── src
│   ├── isotp_errors.py
│   ├── isotp_frame.py
│   ├── isotp_payload_reassembler.py
│   ├── isotp_tx_segmenter.py
│   └── isotp_transport_layer.py
└── tests
    ├── README.md
    ├── test_isotp_transport_layer.py
    └── test_isotp_transport_integration.py
```

<br />

## Root Files

- `isotp_transport_flow_demo.py`: 실제 CAN 없이 FC, BS, STmin 흐름을 확인하는 interactive demo입니다.
- `isotp_transport_demo_common.py`: transport demo의 frame 설명과 출력 형식을 제공하는 공통 helper입니다.
- `isotp_transport_vcan_sender_demo.py`: `vcan0`에서 FC에 따라 multi-frame payload를 전송하는 sender demo입니다.
- `isotp_transport_vcan_interactive_receiver.py`: `vcan0`에서 payload를 수신하며 사용자가 FC 값을 입력하는 receiver demo입니다.

<br />

## Source Files

- `src/isotp_errors.py`: ISO-TP core validation과 transport 공개 예외 계층을 정의합니다.
- `src/isotp_transport_layer.py`: ISO-TP 송수신, Flow Control, CAN ID 필터링, timeout을 관리하는 핵심 계층입니다.
- `src/isotp_frame.py`: CAN data bytes를 ISO-TP frame 객체로 파싱합니다.
- `src/isotp_payload_reassembler.py`: 수신한 SF 또는 FF/CF를 완성된 payload로 조립합니다.
- `src/isotp_tx_segmenter.py`: 송신 payload를 SF 또는 FF/CF data bytes로 분할합니다.

<br />

## Test Files

- `tests/test_isotp_transport_layer.py`: transport layer의 송수신과 예외 흐름을 단위 테스트합니다.
- `tests/test_isotp_transport_integration.py`: virtual bus에서 multi-frame request/response를 통합 테스트합니다.
