"""Send allow/deny MCP tools/call bodies to the live TQA ext_proc server.

TLS, trusting whatever CA signed the presented certificate (this probe
runs from inside the private VPC, same as ``server.py``'s trust model:
the gateway trusts the CA via BackendAuthenticationConfig/TrustConfig
rather than the leaf certificate being self-signed).
"""

from __future__ import annotations

import ssl
import sys

import grpc

from countersign_tqa import external_processor_pb2 as pb2
from countersign_tqa import external_processor_pb2_grpc as pb2_grpc


def _tools_call(*, tenant_id: str, case_tenant_id: str, lifecycle_state: str) -> bytes:
    return (
        '{"jsonrpc":"2.0","id":1,"method":"tools/call",'
        f'"params":{{"name":"lims_query","arguments":{{'
        f'"tenant_id":"{tenant_id}","case_tenant_id":"{case_tenant_id}",'
        f'"lifecycle_state":"{lifecycle_state}","batch_id":"AX-2291"}}}}}}'
    ).encode()


def _channel(host: str) -> grpc.Channel:
    pem = ssl.get_server_certificate((host, 443)).encode()
    creds = grpc.ssl_channel_credentials(root_certificates=pem)
    return grpc.secure_channel(
        f"{host}:443",
        creds,
        options=(("grpc.ssl_target_name_override", host),),
    )


def _process(stub: pb2_grpc.ExternalProcessorStub, body: bytes) -> pb2.ProcessingResponse:
    def requests():
        yield pb2.ProcessingRequest(
            request_body=pb2.HttpBody(body=body, end_of_stream=True)
        )

    return next(stub.Process(requests()))


def main() -> None:
    host = (sys.argv[1] if len(sys.argv) > 1 else "tqa.countersign.internal")
    with _channel(host) as channel:
        stub = pb2_grpc.ExternalProcessorStub(channel)
        allow = _process(
            stub,
            _tools_call(
                tenant_id="aurex",
                case_tenant_id="aurex",
                lifecycle_state="classified",
            ),
        )
        tenant_deny = _process(
            stub,
            _tools_call(
                tenant_id="novara",
                case_tenant_id="aurex",
                lifecycle_state="classified",
            ),
        )
        lifecycle_deny = _process(
            stub,
            _tools_call(
                tenant_id="aurex",
                case_tenant_id="aurex",
                lifecycle_state="dispositioned",
            ),
        )
    if allow.HasField("immediate_response"):
        raise SystemExit("FAIL: matching aurex/classified was denied")
    if not tenant_deny.HasField("immediate_response"):
        raise SystemExit("FAIL: cross-tenant was not denied")
    if b"tenant_mismatch" not in tenant_deny.immediate_response.body:
        raise SystemExit("FAIL: cross-tenant reason missing")
    if not lifecycle_deny.HasField("immediate_response"):
        raise SystemExit("FAIL: dispositioned lifecycle was not denied")
    if b"lifecycle_denied" not in lifecycle_deny.immediate_response.body:
        raise SystemExit("FAIL: lifecycle reason missing")
    print("PASS: allow aurex/classified; deny tenant_mismatch; deny lifecycle_denied")


if __name__ == "__main__":
    main()
