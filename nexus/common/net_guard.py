"""Schutz vor SSRF: ausgehende HTTP(S)-Abrufe nur zu öffentlichen Adressen, mit Größenlimit.

Grenze: Zwischen Prüfung und Verbindungsaufbau kann sich die DNS-Antwort ändern (DNS-Rebinding).
Das verringert, beseitigt das Risiko aber nicht vollständig.
"""
from __future__ import annotations

import ipaddress
import os
import socket
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

_CHUNK = 64 * 1024


class BlockedURL(ValueError):
    pass


def is_public_ip(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr.split("%", 1)[0])  # IPv6-Zonen-ID (fe80::1%eth0) abtrennen
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def check_url(url: str) -> str:
    p = urlparse(url)
    if p.scheme not in ("http", "https"):
        raise BlockedURL("SCHEME_NOT_ALLOWED")
    if not p.hostname:
        raise BlockedURL("HOST_MISSING")
    try:
        port = p.port or (443 if p.scheme == "https" else 80)
    except ValueError:
        raise BlockedURL("BAD_PORT") from None
    try:
        infos = socket.getaddrinfo(p.hostname, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise BlockedURL("DNS_FAILED") from None
    if not infos:
        raise BlockedURL("DNS_FAILED")
    for _family, _type, _proto, _canon, sockaddr in infos:
        addr = str(sockaddr[0])
        if not is_public_ip(addr):
            raise BlockedURL("PRIVATE_ADDRESS:" + addr)
    return url


class _GuardedRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_to_file(url: str, dest: Path, *, timeout: float, max_bytes: int,
                  user_agent: str = "NEXUS-AI/1.0") -> int:
    """Lädt url gestreamt nach dest (über dest.part, atomar umbenannt). Gibt die Byte-Anzahl zurück."""
    check_url(url)
    opener = urllib.request.build_opener(_GuardedRedirect)
    req = urllib.request.Request(url, headers={"User-Agent": user_agent})
    part = dest.with_name(dest.name + ".part")
    total = 0
    try:
        with opener.open(req, timeout=timeout) as resp:
            length = resp.headers.get("Content-Length")
            if length and length.isdigit() and int(length) > max_bytes:
                raise BlockedURL("TOO_LARGE")
            with open(part, "wb") as fh:
                while True:
                    chunk = resp.read(_CHUNK)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        raise BlockedURL("TOO_LARGE")
                    fh.write(chunk)
        os.replace(part, dest)
        return total
    except BaseException:
        part.unlink(missing_ok=True)
        raise
