"""CSRF-Schutz: fremde Browser-Origins dürfen keine POSTs auslösen, native Clients schon."""
from email.message import Message

import pytest

from nexus.chat import server


def _h(**hdrs):
    headers = Message()
    for k, v in hdrs.items():
        headers[k.replace("_", "-")] = v
    return headers


@pytest.mark.parametrize("hdrs,blocked", [
    ({}, False),                                                       # curl / Handy-App
    ({"Host": "127.0.0.1:8299"}, False),
    ({"Host": "127.0.0.1:8299", "Origin": "http://127.0.0.1:8299", "Sec-Fetch-Site": "same-origin"}, False),
    ({"Host": "192.168.1.5:8299", "Origin": "http://192.168.1.5:8299"}, False),  # Handy im LAN-Browser
    ({"Host": "127.0.0.1:8299", "Origin": "https://evil.example"}, True),
    ({"Host": "127.0.0.1:8299", "Sec-Fetch-Site": "cross-site"}, True),
    ({"Host": "127.0.0.1:8299", "Origin": "null"}, True),               # file:// / sandboxed iframe
])
def test_cross_site_blocked(hdrs, blocked):
    assert server._cross_site_blocked(_h(**hdrs)) is blocked


def test_allowlisted_origin_passes(monkeypatch):
    monkeypatch.setattr(server, "_CORS_ORIGINS", frozenset({"http://wozipc-nexus:8299"}))
    assert server._cross_site_blocked(_h(Host="127.0.0.1:8299", Origin="http://wozipc-nexus:8299")) is False
