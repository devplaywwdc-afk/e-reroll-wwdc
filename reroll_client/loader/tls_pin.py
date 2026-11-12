"""SPKI-pinned HTTPS (L1).

The server cert is self-signed (CN=140.99.254.10), so CA validation is off and
we pin the SubjectPublicKeyInfo hash instead. Only our server presents that key;
a MITM / fake server fails the pin before any request body is sent.
"""
from __future__ import annotations

import base64
import hashlib
import http.client
import json
import ssl

from cryptography import x509
from cryptography.hazmat.primitives import serialization


class SSLPinError(Exception):
    """Presented cert's SPKI did not match the pinned value."""


def _spki_sha256_b64(der_cert: bytes) -> str:
    cert = x509.load_der_x509_certificate(der_cert)
    spki = cert.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo)
    return base64.b64encode(hashlib.sha256(spki).digest()).decode("ascii")


class PinnedClient:
    def __init__(self, host: str, port: int, pin_b64: str, timeout: float = 15.0):
        self.host = host
        self.port = port
        self.pin = pin_b64
        self.timeout = timeout

    def request(self, method: str, path: str, body: dict | None = None):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        conn = http.client.HTTPSConnection(
            self.host, self.port, context=ctx, timeout=self.timeout)
        try:
            conn.connect()
            der = conn.sock.getpeercert(binary_form=True)
            got = _spki_sha256_b64(der)
            if got != self.pin:
                raise SSLPinError(f"SPKI pin mismatch (got {got})")
            headers = {}
            data = None
            if body is not None:
                data = json.dumps(body).encode("utf-8")
                headers["Content-Type"] = "application/json"
            conn.request(method, path, body=data, headers=headers)
            resp = conn.getresponse()
            payload = resp.read()
            return resp.status, payload
        finally:
            conn.close()

    def json(self, method: str, path: str, body: dict | None = None):
        status, payload = self.request(method, path, body)
        parsed = None
        if payload:
            try:
                parsed = json.loads(payload)
            except ValueError:
                parsed = None
        return status, parsed
