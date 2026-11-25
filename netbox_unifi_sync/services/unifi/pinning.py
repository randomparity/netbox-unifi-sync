"""HTTP sessions that talk only to a server with one known certificate.

The fingerprint is checked after the TLS handshake and before any request is sent, on every
connection: redirects, retries and logins included. Plain HTTP and proxies are refused.
"""
from __future__ import annotations

import re

import requests
from requests.adapters import HTTPAdapter


# Not a RequestException: the client retries those, and tries other URLs and logins after them.
class CertificatePinError(Exception):
    """Raised when the console's certificate is not the pinned one, or no pin is set."""


def normalize_pin(value: str | None) -> str:
    """Return a SHA-256 fingerprint as 64 lowercase hex digits."""
    pin = str(value or "").replace(":", "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", pin):
        raise CertificatePinError(
            "The console certificate's SHA-256 fingerprint is missing or is not 64 hex digits. "
            "Set cert_sha256 on the controller."
        )
    return pin


class _PinnedAdapter(HTTPAdapter):
    def __init__(self, pin: str):
        self._pin = pin
        super().__init__()

    def init_poolmanager(self, *args, **kwargs):
        super().init_poolmanager(*args, assert_fingerprint=self._pin, **kwargs)

    def proxy_manager_for(self, *args, **kwargs):
        raise CertificatePinError("Refused to reach the console through a proxy.")

    def send(self, *args, **kwargs):
        try:
            return super().send(*args, **kwargs)
        except requests.exceptions.SSLError as err:
            if "Fingerprints did not match" not in str(err):
                raise
            raise CertificatePinError(
                "Refused the console: its certificate is not the pinned one. If it was renewed, "
                f"check the new fingerprint at the console and update cert_sha256. {err}"
            ) from err


class _RefusingAdapter(HTTPAdapter):
    def send(self, *args, **kwargs):
        raise CertificatePinError("Refused a request to the console that is not https.")


def pinned_session(pin: str | None) -> requests.Session:
    session = requests.Session()
    # No proxies, .netrc or CA bundles from the environment.
    session.trust_env = False
    session.mount("https://", _PinnedAdapter(normalize_pin(pin)))
    session.mount("http://", _RefusingAdapter())
    return session
