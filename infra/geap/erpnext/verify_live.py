#!/usr/bin/env python3
"""Read-only production checks for a Countersign ERPNext deployment."""

import argparse
import ipaddress
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request

MIN_ERPNEXT_VERSION = "16.31.1"
EXPECTED_CUSTOM_APP_VERSION = "1.0.0"
EXPECTED_SCHEMA_VERSION = "countersign.erpnext.supply.v1"
TIMEOUT_SECONDS = 15
_METADATA_HOSTS = frozenset({
    "localhost",
    "metadata",
    "metadata.google",
    "metadata.google.internal",
    "169.254.169.254",
})


class VerificationError(RuntimeError):
    """Raised when a live deployment violates an expected invariant."""


def _validate_origin(url):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https":
        raise VerificationError("--url must use HTTPS")
    hostname = parsed.hostname
    if not hostname:
        raise VerificationError("--url must have a hostname")
    if hostname.lower() in _METADATA_HOSTS:
        raise VerificationError("--url must not point at a metadata/loopback host")
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        raise VerificationError("--url must not be an IP literal")
    if parsed.path not in {"", "/"}:
        raise VerificationError("--url must be an HTTPS origin without a path")


class Client:
    def __init__(self, base_url, api_key, api_secret):
        self.base_url = base_url.rstrip("/")
        self.authorization = f"token {api_key}:{api_secret}"
        self.context = ssl.create_default_context()

    def get(self, path, authenticated=True):
        headers = {
            "Accept": "application/json",
            "User-Agent": "countersign-live-verifier/1.0",
        }
        if authenticated:
            headers["Authorization"] = self.authorization
        request = urllib.request.Request(
            f"{self.base_url}{path}", headers=headers, method="GET"
        )
        try:
            with urllib.request.urlopen(
                request, context=self.context, timeout=TIMEOUT_SECONDS
            ) as response:
                body = response.read()
                final_url = response.geturl()
                status = response.status
        except urllib.error.HTTPError as error:
            return error.code, error.read(), error.geturl()
        except urllib.error.URLError as error:
            raise VerificationError(f"request failed for {path}: {error.reason}") from error
        parsed = urllib.parse.urlsplit(final_url)
        expected = urllib.parse.urlsplit(self.base_url)
        if (
            parsed.scheme != "https"
            or (parsed.hostname or "").lower() != (expected.hostname or "").lower()
        ):
            raise VerificationError(f"request escaped the expected HTTPS origin: {path}")
        return status, body, final_url

    def json(self, path, authenticated=True):
        status, body, _ = self.get(path, authenticated=authenticated)
        if status != 200:
            raise VerificationError(f"expected HTTP 200 for {path}, received {status}")
        try:
            return json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise VerificationError(f"invalid JSON returned by {path}") from error


def require_environment(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise VerificationError(f"set {name}")
    return value


def parse_versions(payload):
    versions = {}
    entries = payload.get("message")
    if not isinstance(entries, list):
        raise VerificationError("installed-app version response has an unexpected shape")
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = entry.get("app_name") or entry.get("name")
        version = entry.get("version")
        if isinstance(name, str) and isinstance(version, str):
            versions[name] = version
    return versions


def _version_at_least(actual, minimum):
    def parts(v):
        return [int(x) for x in re.findall(r"\d+", v)]
    return parts(actual)[:3] >= parts(minimum)[:3]


def reject_attachment_exposure(value, path="message"):
    if isinstance(value, dict):
        for key, item in value.items():
            lowered = str(key).lower()
            if lowered in {"file_url", "content", "file_name", "download_url", "attachment_file_id"}:
                raise VerificationError(f"API response exposes raw attachment field {path}.{key}")
            reject_attachment_exposure(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            reject_attachment_exposure(item, f"{path}[{index}]")
    elif isinstance(value, str) and "/private/files/" in value.lower():
        raise VerificationError(f"API response exposes a private attachment path at {path}")


def verify(args):
    _validate_origin(args.url)

    client = Client(
        args.url,
        require_environment("ERPNEXT_API_KEY"),
        require_environment("ERPNEXT_API_SECRET"),
    )
    expected_user = require_environment("ERPNEXT_EXPECTED_USER")
    batch_id = require_environment("ERPNEXT_VERIFY_BATCH_ID")

    identity = client.json("/api/method/frappe.auth.get_logged_user")
    if identity.get("message") != expected_user:
        raise VerificationError("authenticated API identity does not match ERPNEXT_EXPECTED_USER")

    versions = parse_versions(client.json("/api/method/frappe.utils.change_log.get_versions"))
    erpnext_version = versions.get("erpnext")
    if not erpnext_version or not _version_at_least(erpnext_version, MIN_ERPNEXT_VERSION):
        raise VerificationError(
            f"ERPNext version is {erpnext_version!r}, expected >={MIN_ERPNEXT_VERSION}"
        )
    if versions.get("countersign_erpnext") != EXPECTED_CUSTOM_APP_VERSION:
        raise VerificationError(
            "Countersign app version is "
            f"{versions.get('countersign_erpnext')!r}, expected {EXPECTED_CUSTOM_APP_VERSION}"
        )

    query = urllib.parse.urlencode({"batch_id": batch_id})
    snapshot = client.json(
        "/api/method/countersign_erpnext.api.v1.get_coa_snapshot?" + query
    )
    message = snapshot.get("message")
    if not isinstance(message, dict):
        raise VerificationError("snapshot response has an unexpected shape")
    if message.get("schema_version") != EXPECTED_SCHEMA_VERSION:
        raise VerificationError("snapshot schema version does not match the deployed contract")
    if message.get("batch_id") != batch_id:
        raise VerificationError("snapshot batch identity does not match the requested batch")
    if not re.fullmatch(r"[0-9a-f]{64}", message.get("gmp_certificate_sha256", "")):
        raise VerificationError("snapshot does not carry a pinned GMP certificate SHA-256")
    reject_attachment_exposure(message)

    siblings = client.json(
        "/api/method/countersign_erpnext.api.v1.get_batches_sharing_material_lot?" + query
    )
    sibling_message = siblings.get("message")
    if not isinstance(sibling_message, dict):
        raise VerificationError("sibling response has an unexpected shape")
    if sibling_message.get("schema_version") != EXPECTED_SCHEMA_VERSION:
        raise VerificationError("sibling schema version does not match the deployed contract")
    if sibling_message.get("batch_id") != batch_id:
        raise VerificationError("sibling batch identity does not match the requested batch")
    for sibling in sibling_message.get("siblings", []):
        sibling_uri = sibling.get("source_uri", "")
        if not sibling_uri.startswith(args.url.rstrip("/")):
            raise VerificationError("sibling source_uri escaped the configured origin")
        if "/app/stock-entry/" not in sibling_uri and "/app/countersign-coa-verification/" not in sibling_uri:
            raise VerificationError("sibling source_uri path is not an allowed Desk route")
    reject_attachment_exposure(sibling_message)

    print(
        json.dumps(
            {
                "app_version": EXPECTED_CUSTOM_APP_VERSION,
                "erpnext_version": erpnext_version,
                "identity": expected_user,
                "schema_version": EXPECTED_SCHEMA_VERSION,
                "sibling_count": len(sibling_message.get("siblings", [])),
                "status": "ok",
                "url": args.url.rstrip("/"),
            },
            sort_keys=True,
        )
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="ERPNext HTTPS origin")
    args = parser.parse_args()
    try:
        verify(args)
    except VerificationError as error:
        print(f"verification failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
