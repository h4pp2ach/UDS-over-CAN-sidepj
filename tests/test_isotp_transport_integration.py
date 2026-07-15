from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import can

from isotp_transport_layer import IsoTpTransportLayer


# 기능 그룹: python-can virtual bus 기반 ISO-TP 통합 송수신
# 검증 목적: 실제 python-can Bus 두 개 사이에서 FF/FC/CF와 Block Size가 연결되어 payload가 왕복하는지 확인한다.
def test_virtual_bus_transports_exchange_multi_frame_payloads():
    channel = f"isotp-integration-{uuid4()}"
    client_bus = can.Bus(
        interface="virtual",
        channel=channel,
        receive_own_messages=False,
    )
    server_bus = can.Bus(
        interface="virtual",
        channel=channel,
        receive_own_messages=False,
    )
    client = IsoTpTransportLayer.for_client(
        client_bus,
        request_can_id=0x7E0,
        response_can_id=0x7E8,
        frame_timeout_seconds=1.0,
        padding_byte=0x00,
    )
    server = IsoTpTransportLayer.for_server(
        server_bus,
        request_can_id=0x7E0,
        response_can_id=0x7E8,
        block_size=2,
        frame_timeout_seconds=1.0,
        padding_byte=0x00,
    )
    request_payload = bytes(range(1, 51))
    response_payload = bytes(range(101, 141))

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            request_future = executor.submit(server.recv)
            client.send(request_payload)

            assert request_future.result(timeout=2.0) == request_payload

            response_future = executor.submit(client.recv)
            server.send(response_payload)

            assert response_future.result(timeout=2.0) == response_payload
    finally:
        client_bus.shutdown()
        server_bus.shutdown()
