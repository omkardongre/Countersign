# Copyright 2026 Google LLC
"""TLS HTTP/2 ext_proc gRPC server on 443, with a CA-signed certificate.

Root cause of the live-only MCP ``tools/call`` 400 (found 2026-08-17): the
gateway's real Envoy client performs standard TLS certificate verification
against this callout and rejected the previous self-signed certificate
with ``FAILED_PRECONDITION``, so TQA silently failed open with an
empty/broken body forwarded downstream, corrupting the MCP request. A
manual ``openssl s_client`` probe did not reproduce this because it does
not verify by default; a real gRPC client (matching Envoy's behavior)
does, and failed identically with
``CERTIFICATE_VERIFY_FAILED: self signed certificate``.

Fix: serve a leaf certificate signed by a private CA, and register that
CA's certificate with the gateway's ``BackendAuthenticationConfig``
(Certificate Manager ``TrustConfig``) on the backend service in front of
this VM (``countersign-tqa-backend``), so the gateway explicitly trusts
this CA instead of relying on public trust. A bare, unauthenticated
authzExtension ``service`` FQDN is always dialed with TLS by the gateway
(confirmed live: an H2C attempt against a bare FQDN produced
``UNAVAILABLE`` rather than succeeding), so removing TLS was not a viable
path for this resource type; a real trust chain is required instead.
"""

from __future__ import annotations

import os
from concurrent import futures

import grpc
from grpc_health.v1 import health, health_pb2, health_pb2_grpc

from countersign_tqa import external_processor_pb2_grpc as pb2_grpc
from countersign_tqa.processor import ContinueProcessor

_DEFAULT_PORT = os.environ.get("TQA_PORT", "443")
_DEFAULT_CERT = os.environ.get("TQA_TLS_CERT", "/etc/tqa/tls.crt")
_DEFAULT_KEY = os.environ.get("TQA_TLS_KEY", "/etc/tqa/tls.key")


def _build_server() -> grpc.Server:
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
    pb2_grpc.add_ExternalProcessorServicer_to_server(ContinueProcessor(), server)
    health_servicer = health.HealthServicer()
    health_servicer.set("", health_pb2.HealthCheckResponse.SERVING)
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)
    return server


def serve(
    *,
    bind: str | None = None,
    cert_path: str = _DEFAULT_CERT,
    key_path: str = _DEFAULT_KEY,
) -> None:
    bind = bind or f"0.0.0.0:{_DEFAULT_PORT}"
    server = _build_server()
    cert = open(cert_path, "rb").read()
    key = open(key_path, "rb").read()
    creds = grpc.ssl_server_credentials([(key, cert)])
    server.add_secure_port(bind, creds)
    server.start()
    server.wait_for_termination()


def main() -> None:
    serve()


if __name__ == "__main__":
    main()
