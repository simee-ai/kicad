"""Downloads shared by the packagers (kicad_bundle/fetch.py)."""

import ssl

import certifi

from kicad_bundle import fetch


class _Response:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return b"data"


def test_downloads_trust_certifis_roots_not_the_os_bundle(monkeypatch):
    """macOS's /etc/ssl/cert.pem lacks roots its Keychain has, such as GlobalSign Root R46, which signs
    gitlab.arm.com, where Arm's toolchain downloads are from 15.3: certifi's bundle is the same everywhere."""
    seen = {}

    def urlopen(req, timeout, context):
        seen["context"] = context
        return _Response()

    monkeypatch.setattr(fetch.urllib.request, "urlopen", urlopen)
    assert fetch.fetch_url("https://gitlab.arm.com/x") == b"data"
    context = seen["context"]
    assert isinstance(context, ssl.SSLContext) and context.verify_mode == ssl.CERT_REQUIRED
    assert context.cert_store_stats()["x509_ca"] >= len(open(certifi.where()).read().split("BEGIN CERTIFICATE")) - 1
