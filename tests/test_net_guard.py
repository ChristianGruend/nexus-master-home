"""SSRF-Schutz (Stufe 2.6): nur öffentliche Ziele, Redirect-Prüfung, Größenlimit – ohne echtes Netz."""
import io
import socket

import pytest

from nexus.common import net_guard
from nexus.common.net_guard import BlockedURL, check_url, is_public_ip
from nexus.downloads.downloader import download_url


@pytest.mark.parametrize("addr", ["8.8.8.8", "2606:4700:4700::1111"])
def test_public(addr):
    assert is_public_ip(addr)


@pytest.mark.parametrize("addr", ["127.0.0.1", "10.0.0.1", "192.168.178.1", "172.16.0.1", "100.64.0.5",
                                  "169.254.169.254", "::1", "fe80::1", "fe80::1%eth0", "::ffff:127.0.0.1",
                                  "0.0.0.0", "224.0.0.1", "kein-ip"])
def test_not_public(addr):
    assert not is_public_ip(addr)


def _dns(monkeypatch, *ips):
    def fake(host, port, *a, **kw):
        return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))
                for ip in ips]
    monkeypatch.setattr(net_guard.socket, "getaddrinfo", fake)


def test_public_url_passes(monkeypatch):
    _dns(monkeypatch, "93.184.215.14")
    assert check_url("https://example.org/a") == "https://example.org/a"


def test_private_url_blocked(monkeypatch):
    _dns(monkeypatch, "127.0.0.1")
    with pytest.raises(BlockedURL, match="PRIVATE_ADDRESS"):
        check_url("http://localhost.example/x")


def test_mixed_answer_blocked(monkeypatch):
    _dns(monkeypatch, "93.184.215.14", "10.0.0.5")
    with pytest.raises(BlockedURL):
        check_url("http://rebind.example/x")


@pytest.mark.parametrize("url", ["ftp://x/y", "file:///etc/passwd", "gopher://x"])
def test_scheme_blocked(url):
    with pytest.raises(BlockedURL, match="SCHEME_NOT_ALLOWED"):
        check_url(url)


def test_dns_failure(monkeypatch):
    def boom(*a, **kw):
        raise socket.gaierror("nope")
    monkeypatch.setattr(net_guard.socket, "getaddrinfo", boom)
    with pytest.raises(BlockedURL, match="DNS_FAILED"):
        check_url("http://does-not-exist.example/")


def test_redirect_to_private_blocked(monkeypatch):
    _dns(monkeypatch, "10.0.0.5")
    with pytest.raises(BlockedURL):
        net_guard._GuardedRedirect().redirect_request(None, None, 302, "Found", {}, "http://intern.example/")


class _Resp(io.BytesIO):
    def __init__(self, data, length=None):
        super().__init__(data)
        self.headers = {"Content-Length": str(length)} if length is not None else {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _opener(monkeypatch, resp):
    class O:
        def open(self, req, timeout):
            return resp
    monkeypatch.setattr(net_guard.urllib.request, "build_opener", lambda *a: O())


def test_fetch_streams_and_renames(monkeypatch, tmp_path):
    _dns(monkeypatch, "93.184.215.14")
    _opener(monkeypatch, _Resp(b"x" * 200_000))
    dest = tmp_path / "f.bin"
    assert net_guard.fetch_to_file("https://example.org/f", dest, timeout=5, max_bytes=1_000_000) == 200_000
    assert dest.read_bytes() == b"x" * 200_000 and not (tmp_path / "f.bin.part").exists()


def test_fetch_too_large_by_header(monkeypatch, tmp_path):
    _dns(monkeypatch, "93.184.215.14")
    _opener(monkeypatch, _Resp(b"x", length=10_000))
    with pytest.raises(BlockedURL, match="TOO_LARGE"):
        net_guard.fetch_to_file("https://example.org/f", tmp_path / "f", timeout=5, max_bytes=100)
    assert list(tmp_path.iterdir()) == []


def test_fetch_too_large_while_streaming(monkeypatch, tmp_path):
    _dns(monkeypatch, "93.184.215.14")
    _opener(monkeypatch, _Resp(b"x" * 300_000))
    with pytest.raises(BlockedURL, match="TOO_LARGE"):
        net_guard.fetch_to_file("https://example.org/f", tmp_path / "f", timeout=5, max_bytes=100_000)
    assert list(tmp_path.iterdir()) == []


def test_downloader_blocks_private(monkeypatch, tmp_path):
    _dns(monkeypatch, "127.0.0.1")
    r = download_url("http://127.0.0.1/x", tmp_path)
    assert r["success"] is False and r["error"].startswith("BLOCKED:")
    assert list(tmp_path.iterdir()) == []
