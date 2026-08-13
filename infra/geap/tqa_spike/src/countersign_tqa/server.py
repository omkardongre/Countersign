# Copyright 2026 Google LLC
"""TLS HTTP/2 ext_proc hello on 443. No TQA policy logic."""

from __future__ import annotations

import os
import subprocess
from concurrent import futures

import grpc
from grpc_health.v1 import health, health_pb2, health_pb2_grpc

from countersign_tqa import external_processor_pb2_grpc as pb2_grpc
from countersign_tqa.processor import ContinueProcessor

_DEFAULT_CERT = os.environ.get("TQA_TLS_CERT", "/etc/tqa/tls.crt")
_DEFAULT_KEY = os.environ.get("TQA_TLS_KEY", "/etc/tqa/tls.key")
_DEFAULT_FQDN = os.environ.get("TQA_FQDN", "tqa.countersign.internal")


def ensure_self_signed_cert(cert_path: str, key_path: str, fqdn: str) -> None:
    """Create a self-signed cert if missing. The gateway does not verify it."""
    if os.path.exists(cert_path) and os.path.exists(key_path):
        return
    os.makedirs(os.path.dirname(cert_path) or ".", exist_ok=True)
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-sha256",
            "-days",
            "365",
            "-nodes",
            "-keyout",
            key_path,
            "-out",
            cert_path,
            "-subj",
            f"/CN={fqdn}",
            "-addext",
            f"subjectAltName=DNS:{fqdn}",
        ],
        check=True,
        capture_output=True,
    )


def serve(
    *,
    bind: str = "0.0.0.0:443",
    cert_path: str = _DEFAULT_CERT,
    key_path: str = _DEFAULT_KEY,
    fqdn: str = _DEFAULT_FQDN,
) -> None:
    ensure_self_signed_cert(cert_path, key_path, fqdn)
    cert = open(cert_path, "rb").read()
    key = open(key_path, "rb").read()
    creds = grpc.ssl_server_credentials([(key, cert)])
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
    pb2_grpc.add_ExternalProcessorServicer_to_server(ContinueProcessor(), server)
    health_servicer = health.HealthServicer()
    health_servicer.set("", health_pb2.HealthCheckResponse.SERVING)
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)
    server.add_secure_port(bind, creds)
    server.start()
    server.wait_for_termination()


def main() -> None:
    serve()


if __name__ == "__main__":
    main()
