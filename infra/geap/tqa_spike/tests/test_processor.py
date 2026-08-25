from countersign_tqa import external_processor_pb2 as pb2
from countersign_tqa.mcp_payload import parse_mcp_tool_call
from countersign_tqa.policy import (
    REASON_LIFECYCLE_DENIED,
    REASON_MISSING_TUPLE,
    REASON_TENANT_MISMATCH,
    authorize_department_read,
)
from countersign_tqa.processor import (
    continue_response,
    decide_request_body,
    deny_response,
)


def _tools_call(
    *,
    name: str = "lims_query",
    tenant_id: str = "aurex",
    case_tenant_id: str = "aurex",
    lifecycle_state: str = "classified",
) -> bytes:
    return (
        '{"jsonrpc":"2.0","id":1,"method":"tools/call",'
        f'"params":{{"name":"{name}","arguments":{{'
        f'"tenant_id":"{tenant_id}","case_tenant_id":"{case_tenant_id}",'
        f'"lifecycle_state":"{lifecycle_state}","batch_id":"AX-2291"}}}}}}'
    ).encode()


def test_continue_maps_each_ext_proc_event() -> None:
    assert continue_response(
        pb2.ProcessingRequest(request_headers=pb2.HttpHeaders())
    ).HasField("request_headers")
    assert continue_response(
        pb2.ProcessingRequest(response_headers=pb2.HttpHeaders())
    ).HasField("response_headers")
    assert continue_response(
        pb2.ProcessingRequest(request_body=pb2.HttpBody())
    ).HasField("request_body")
    assert continue_response(
        pb2.ProcessingRequest(response_body=pb2.HttpBody())
    ).HasField("response_body")
    assert continue_response(
        pb2.ProcessingRequest(request_trailers=pb2.HttpTrailers())
    ).HasField("request_trailers")
    assert continue_response(
        pb2.ProcessingRequest(response_trailers=pb2.HttpTrailers())
    ).HasField("response_trailers")


def test_serve_uses_ca_signed_certificate(tmp_path) -> None:
    """A real gRPC/Envoy client verifies TLS certificates; a self-signed
    one was rejected with FAILED_PRECONDITION, which was the root cause
    of the live-only MCP ``tools/call`` 400. This test proves the server
    presents exactly the certificate file it was given (a CA-signed leaf
    in production), by connecting a gRPC client that trusts only that
    file's issuing CA.
    """
    import subprocess
    import threading

    import grpc
    from countersign_tqa import server as server_module

    ca_key = tmp_path / "ca.key"
    ca_crt = tmp_path / "ca.crt"
    leaf_key = tmp_path / "leaf.key"
    leaf_csr = tmp_path / "leaf.csr"
    leaf_crt = tmp_path / "leaf.crt"

    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-sha256",
            "-days", "1", "-nodes", "-keyout", str(ca_key), "-out", str(ca_crt),
            "-subj", "/CN=test-ca",
            "-addext", "basicConstraints=critical,CA:TRUE",
        ],
        check=True, capture_output=True,
    )
    subprocess.run(
        [
            "openssl", "req", "-newkey", "rsa:2048", "-nodes",
            "-keyout", str(leaf_key), "-out", str(leaf_csr),
            "-subj", "/CN=localhost",
            "-addext", "subjectAltName=DNS:localhost",
        ],
        check=True, capture_output=True,
    )
    subprocess.run(
        [
            "openssl", "x509", "-req", "-in", str(leaf_csr),
            "-CA", str(ca_crt), "-CAkey", str(ca_key), "-CAcreateserial",
            "-out", str(leaf_crt), "-days", "1", "-sha256",
            "-extfile", "/dev/stdin",
        ],
        input=b"subjectAltName=DNS:localhost",
        check=True, capture_output=True,
    )

    server = server_module._build_server()
    creds = grpc.ssl_server_credentials(
        [(leaf_key.read_bytes(), leaf_crt.read_bytes())]
    )
    port = server.add_secure_port("127.0.0.1:0", creds)
    assert port > 0
    server.start()
    thread = threading.Thread(target=server.wait_for_termination, daemon=True)
    thread.start()
    try:
        channel_creds = grpc.ssl_channel_credentials(
            root_certificates=ca_crt.read_bytes()
        )
        channel = grpc.secure_channel(
            f"localhost:{port}",
            channel_creds,
            options=(("grpc.ssl_target_name_override", "localhost"),),
        )
        grpc.channel_ready_future(channel).result(timeout=5)
    finally:
        server.stop(None)


def test_allow_matching_aurex_classified() -> None:
    decision = authorize_department_read(parse_mcp_tool_call(_tools_call()))
    assert decision.allow is True


def test_deny_cross_tenant() -> None:
    decision = authorize_department_read(
        parse_mcp_tool_call(
            _tools_call(tenant_id="novara", case_tenant_id="aurex")
        )
    )
    assert decision.allow is False
    assert decision.reason == REASON_TENANT_MISMATCH


def test_deny_dispositioned_lifecycle() -> None:
    decision = authorize_department_read(
        parse_mcp_tool_call(_tools_call(lifecycle_state="dispositioned"))
    )
    assert decision.allow is False
    assert decision.reason == REASON_LIFECYCLE_DENIED


def test_deny_missing_lifecycle() -> None:
    body = (
        b'{"jsonrpc":"2.0","method":"tools/call","params":{"name":"lims_query",'
        b'"arguments":{"tenant_id":"aurex","case_tenant_id":"aurex"}}}'
    )
    decision = authorize_department_read(parse_mcp_tool_call(body))
    assert decision.allow is False
    assert decision.reason == REASON_MISSING_TUPLE


def test_initialize_is_not_a_tool_call() -> None:
    assert parse_mcp_tool_call(b'{"jsonrpc":"2.0","method":"initialize","id":1}') is None


def test_unrelated_mcp_tool_is_not_a_tqa_deny() -> None:
    body = (
        b'{"jsonrpc":"2.0","method":"tools/call","params":{"name":"get_weather"}}'
    )
    decision = authorize_department_read(parse_mcp_tool_call(body))
    assert decision.allow is True
    response = decide_request_body(body)
    assert response.HasField("request_body")


def test_tools_call_without_name_fails_closed() -> None:
    body = b'{"jsonrpc":"2.0","method":"tools/call","params":{}}'
    decision = authorize_department_read(parse_mcp_tool_call(body))
    assert decision.allow is False
    assert decision.reason == REASON_MISSING_TUPLE


def test_body_decision_denies_with_immediate_response() -> None:
    response = decide_request_body(
        _tools_call(tenant_id="novara", case_tenant_id="aurex")
    )
    assert response.HasField("immediate_response")
    assert response.immediate_response.status.code == 403
    assert b"tenant_mismatch" in response.immediate_response.body


def test_body_decision_allows_matching_tuple() -> None:
    response = decide_request_body(_tools_call())
    assert response.HasField("request_body")
    assert not response.HasField("immediate_response")


def test_non_mcp_body_continues() -> None:
    response = decide_request_body(b'{"prompt":"hello"}')
    assert response.HasField("request_body")


def test_deny_response_shape() -> None:
    response = deny_response("tqa_lifecycle_denied")
    assert response.immediate_response.details == "tqa_lifecycle_denied"
