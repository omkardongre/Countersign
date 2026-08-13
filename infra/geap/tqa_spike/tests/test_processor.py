from countersign_tqa import external_processor_pb2 as pb2
from countersign_tqa.mcp_payload import parse_mcp_tool_call
from countersign_tqa.policy import (
    REASON_LIFECYCLE_DENIED,
    REASON_MISSING_TUPLE,
    REASON_TENANT_MISMATCH,
    authorize_department_read,
)
from countersign_tqa.processor import continue_response, decide_request_body, deny_response


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


def test_self_signed_cert_uses_fqdn(tmp_path) -> None:
    from countersign_tqa.server import ensure_self_signed_cert

    cert = tmp_path / "tls.crt"
    key = tmp_path / "tls.key"
    ensure_self_signed_cert(str(cert), str(key), "tqa.countersign.internal")
    assert cert.read_bytes()
    assert key.read_bytes()


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
