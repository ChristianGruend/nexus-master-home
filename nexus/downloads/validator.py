from __future__ import annotations
from urllib.parse import urlparse

def validate_url(url: str) -> tuple[bool, str]:
    u = (url or "").strip()
    p = urlparse(u)
    if p.scheme not in ("http", "https"):
        return False, "Nur http/https erlaubt."
    if not p.netloc:
        return False, "Ungültige URL."
    return True, u
