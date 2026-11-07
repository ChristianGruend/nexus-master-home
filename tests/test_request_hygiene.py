import pytest

from nexus.chat.server import _host_allowed


@pytest.mark.parametrize("host", [
    "",
    "localhost:8299",
    "127.0.0.1:8299",
    "[::1]:8299",
    "192.168.178.20",
    "wozipc-nexus:8299",
    "pc.tail1234.ts.net",
    "nexus.local:8299",
    "LOCALHOST.",
])
def test_host_allowed_true(host: str) -> None:
    assert _host_allowed(host, frozenset()) is True


@pytest.mark.parametrize("host", [
    "evil.example.com",
    "rebind.attacker.net:8299",
    "127.0.0.1.nip.io",
])
def test_host_allowed_false(host: str) -> None:
    assert _host_allowed(host, frozenset()) is False


def test_host_allowed_extra() -> None:
    assert _host_allowed("nexus.example.org", frozenset()) is False
    assert _host_allowed("nexus.example.org", frozenset({"nexus.example.org"})) is True
