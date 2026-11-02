from __future__ import annotations
import re
import html as htmllib
import base64
import urllib.parse
import urllib.request
import json
import time


class Research:
    """Live Web-Suche: DuckDuckGo HTML, Fallback Bing. Snippets+URLs, kein Fake."""

    STOP = set("der die das dem den des ein eine einen einem und oder mit von zu zum zur im in am an auf für fuer über ueber nach was wie ist sind".split())

    def _terms(self, q: str) -> list[str]:
        words = re.findall(r"[\wäöüß]{3,}", (q or "").lower())
        return [w[:5] if len(w) > 5 else w for w in words if w not in self.STOP]

    def _relevance(self, r: dict, terms: list[str]) -> float:
        if not terms:
            return 1.0
        hay = " ".join([str(r.get("title") or ""), str(r.get("snippet") or ""),
                        urllib.parse.unquote(str(r.get("url") or ""))]).lower()
        return sum(1 for t in terms if t in hay) / len(terms)

    def search(self, query: str, limit: int = 6, news: bool = False) -> dict:
        """Mehrstufig: DDG-HTML (mit Backoff) -> DDG-Lite -> Bing News + Bing -> Wikipedia.
        Nur Treffer, die die Suchbegriffe wirklich enthalten (Relevanzfilter); nichts erfunden."""
        q = (query or "").strip()
        if not q:
            return {
                "success": False,
                "blocked": True,
                "error": "leere Query",
                "results": [],
                "reply": "BLOCKED: Keine Suchanfrage - wonach soll ich im Web suchen?",
            }
        terms = self._terms(q)
        need = 1.0 if len(terms) <= 3 else 0.6
        tried: list[str] = []
        err = None
        news_intent = news or any(token in q.lower() for token in ("heute", "news", "nachrichten", "welt", "aktuell", "passiert"))

        def ddg():
            for attempt in range(2):
                html, e = self._fetch_ddg(q)
                if html and not self._is_bot_wall(html):
                    return self._parse_ddg(html, limit * 2), None
                if attempt == 0:
                    time.sleep(1.5)
            return [], e or "ddg_bot_wall"

        def ddg_lite():
            html, e = self._fetch_ddg_lite(q)
            if html and not self._is_bot_wall(html):
                return self._parse_ddg_lite(html, limit * 2), None
            return [], e or "ddg_lite_bot_wall"

        def bing_news():
            html, e = self._fetch_bing_news(q)
            found = self._parse_bing_news(html, limit * 2) if html else []
            if not [r for r in found if self._relevance(r, terms) >= need]:
                html, e = self._fetch_bing_news(q, recent=False)
                found = self._parse_bing_news(html, limit * 2) if html else []
            return found, e

        def bing():
            html, e = self._fetch_bing(q)
            return (self._parse_bing(html, limit * 2) if html else []), e

        def wiki():
            return self._wikipedia(q, limit), None

        stages = [("DuckDuckGo", ddg), ("DuckDuckGo Lite", ddg_lite)]
        if news:  # "heute"/neu/aktuell: Datumsbezug -> Bing News (7 Tage) zuerst
            stages.insert(0, ("Bing News", bing_news))
        stages += ([("Bing News", bing_news), ("Bing", bing)] if news_intent else [("Bing", bing), ("Bing News", bing_news)])
        stages.append(("Wikipedia", wiki))
        source = None
        results: list[dict] = []
        for name, fn in stages:
            try:
                found, e = fn()
            except Exception as exc:  # Netzfehler je Stufe, naechste Stufe versuchen
                found, e = [], str(exc)
            tried.append(name)
            if e and not err:
                err = e
            found = [r for r in found if self._is_content_result(r) and self._relevance(r, terms) >= need]
            if found:
                seen = set()
                results = [r for r in found if not (r["url"] in seen or seen.add(r["url"]))][:limit]
                source = name
                break

        if not results:
            return {
                "success": False,
                "blocked": False,
                "error": err or "no_verified_results",
                "results": [],
                "tried": tried,
                "reply": (
                    f"SEARCH_FAIL: Keine passenden Web-Treffer für \"{q}\" (geprüft: {', '.join(tried)}). "
                    "Unpassende Treffer werden nicht ausgegeben."
                ),
            }

        lines = [f"Live-Web ({source}) - nur Treffer, nichts erfunden:", f"Query: {q}", ""]
        for i, r in enumerate(results, 1):
            snip = (r.get("snippet") or "").strip()
            lines.append(f"{i}. {r['title']}")
            if snip:
                lines.append(f"   {snip}")
            lines.append(f"   {r['url']}")
        lines.append("")
        lines.append("Quelle: Live-Suche. Kein Knowledge-Cutoff-Theater, keine Fake-Schlagzeilen.")
        return {
            "success": True,
            "blocked": False,
            "results": results,
            "reply": "\n".join(lines),
            "query": q,
            "source": source,
            "tried": tried,
        }

    def _fetch_ddg_lite(self, q: str) -> tuple[str | None, str | None]:
        try:
            data = urllib.parse.urlencode({"q": q}).encode()
            req = urllib.request.Request("https://lite.duckduckgo.com/lite/", data=data, headers=self._headers())
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.read().decode("utf-8", errors="replace"), None
        except Exception as e:
            return None, str(e)

    def _parse_ddg_lite(self, html: str, limit: int) -> list[dict]:
        out: list[dict] = []
        for m in re.finditer(r"<a[^>]+href=\"([^\"]+)\"[^>]*class=['\"]result-link['\"][^>]*>(.*?)</a>", html or "", re.I | re.S):
            href = htmllib.unescape(m.group(1))
            if "uddg=" in href:
                qs = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
                href = qs.get("uddg", [href])[0]
            title = htmllib.unescape(re.sub(r"<[^>]+>", "", m.group(2))).strip()
            if title and href.startswith("http"):
                out.append({"title": title, "url": href, "snippet": ""})
            if len(out) >= limit:
                break
        return out

    def _parse_bing_news(self, html: str, limit: int) -> list[dict]:
        out: list[dict] = []
        for m in re.finditer(r"<a[^>]*class=\"title\"[^>]*href=\"([^\"]+)\"[^>]*>(.*?)</a>", html or "", re.I | re.S):
            href = htmllib.unescape(m.group(1))
            title = htmllib.unescape(re.sub(r"<[^>]+>", "", m.group(2))).strip()
            sm = re.search(r"class=\"snippet\"[^>]*title=\"([^\"]*)\"", (html or "")[m.end():m.end() + 4000])
            snip = htmllib.unescape(sm.group(1)).strip() if sm else ""
            if title and href.startswith("http"):
                out.append({"title": re.sub(r"\s+", " ", title), "url": href, "snippet": snip})
            if len(out) >= limit:
                break
        return out or self._parse_bing(html, limit)

    def _wikipedia(self, q: str, limit: int) -> list[dict]:
        url = ("https://de.wikipedia.org/w/api.php?action=query&list=search&format=json&srlimit="
               + str(int(limit)) + "&srsearch=" + urllib.parse.quote(q))
        req = urllib.request.Request(url, headers=self._headers())
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        out = []
        for it in (data.get("query") or {}).get("search") or []:
            title = str(it.get("title") or "")
            snip = htmllib.unescape(re.sub(r"<[^>]+>", "", str(it.get("snippet") or "")))
            out.append({"title": title, "snippet": snip,
                        "url": "https://de.wikipedia.org/wiki/" + urllib.parse.quote(title.replace(" ", "_"))})
        return out

    def _is_content_result(self, result: dict) -> bool:
        url = str((result or {}).get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            return False
        try:
            host = (urllib.parse.urlparse(url).hostname or "").lower().strip(".")
        except Exception:
            return False
        if not host:
            return False
        blocked_hosts = (
            "duckduckgo.com", "www.duckduckgo.com", "html.duckduckgo.com",
            "bing.com", "www.bing.com", "google.com", "www.google.com",
            "search.yahoo.com", "yahoo.com",
        )
        if host in blocked_hosts or host.endswith(".duckduckgo.com") or host.endswith(".bing.com"):
            return False
        return True

    def _headers(self) -> dict:
        return {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }

    def _is_bot_wall(self, html: str) -> bool:
        low = (html or "").lower()
        if "anomaly-modal" in low or "bot challenge" in low:
            return True
        if "result__a" not in low and "uddg=" not in low and ("anomaly" in low or "sorry" in low):
            return True
        return False

    def _fetch_ddg(self, q: str) -> tuple[str | None, str | None]:
        url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(q)
        try:
            req = urllib.request.Request(url, headers=self._headers())
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.read().decode("utf-8", errors="replace"), None
        except Exception as e:
            return None, str(e)

    def _fetch_bing_news(self, q: str, recent: bool = True) -> tuple[str | None, str | None]:
        url = "https://www.bing.com/news/search?q=" + urllib.parse.quote(q) + "&setlang=de-DE" + ("&qft=interval%3d%227%22" if recent else "")
        try:
            req = urllib.request.Request(url, headers=self._headers())
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.read().decode("utf-8", errors="replace"), None
        except Exception as e:
            return None, str(e)

    def _fetch_bing(self, q: str) -> tuple[str | None, str | None]:
        url = "https://www.bing.com/search?q=" + urllib.parse.quote(q) + "&setlang=de-DE"
        try:
            req = urllib.request.Request(url, headers=self._headers())
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.read().decode("utf-8", errors="replace"), None
        except Exception as e:
            return None, str(e)

    def _bing_dest(self, href: str, cite_txt: str = "") -> str | None:
        href = (href or "").replace("&amp;", "&")
        m = re.search(r"[?&]u=([^&]+)", href)
        if m:
            raw = urllib.parse.unquote(m.group(1))
            if raw.startswith("a1"):
                raw = raw[2:]
            pad = "=" * ((4 - len(raw) % 4) % 4)
            for candidate in (raw, raw + pad):
                try:
                    dec = base64.urlsafe_b64decode(candidate.encode("ascii", errors="ignore"))
                    s = dec.decode("utf-8", errors="replace")
                    if s.startswith("http"):
                        return s.split()[0]
                except Exception:
                    pass
        if href.startswith("http") and "bing.com/ck" not in href and "microsoft.com" not in href:
            return href
        if cite_txt:
            c = cite_txt.replace(" › ", "/").split()[0].strip()
            c = c.split("·")[0].strip()
            if c.startswith("http"):
                return c
            if re.match(r"[\w.-]+\.[a-z]{2,}", c, re.I):
                return "https://" + c
        return None

    def _parse_bing(self, html: str, limit: int) -> list[dict]:
        results: list[dict] = []
        blocks = re.split(r'class="b_algo"', html, flags=re.I)
        for block in blocks[1:]:
            am = re.search(
                r'<h2[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
                block,
                re.I | re.S,
            )
            if not am:
                continue
            title = htmllib.unescape(re.sub(r"<[^>]+>", "", am.group(2))).strip()
            title = re.sub(r"\s+", " ", title)
            cite_m = re.search(r"<cite[^>]*>(.*?)</cite>", block, re.I | re.S)
            cite_txt = (
                htmllib.unescape(re.sub(r"<[^>]+>", "", cite_m.group(1))).strip()
                if cite_m
                else ""
            )
            href = self._bing_dest(am.group(1), cite_txt)
            if not title or not href or not href.startswith("http"):
                continue
            snip_m = re.search(
                r'class="b_lineclamp\d+"[^>]*>(.*?)</(?:p|div)>',
                block,
                re.I | re.S,
            )
            if not snip_m:
                snip_m = re.search(r"<p[^>]*>(.*?)</p>", block, re.I | re.S)
            snippet = ""
            if snip_m:
                snippet = htmllib.unescape(re.sub(r"<[^>]+>", "", snip_m.group(1))).strip()
                snippet = re.sub(r"\s+", " ", snippet)
            results.append({"title": title, "url": href, "snippet": snippet})
            if len(results) >= limit:
                break
        return results

    def _parse_ddg(self, html: str, limit: int) -> list[dict]:
        results: list[dict] = []
        blocks = re.split(r'class="result__body"|class="result results_links', html, flags=re.I)
        for block in blocks:
            am = re.search(
                r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
                block,
                re.I | re.S,
            )
            if not am:
                am = re.search(
                    r'uddg=([^&"]+).*?class="result__a"[^>]*>(.*?)</a>',
                    block,
                    re.I | re.S,
                )
                if am:
                    href = urllib.parse.unquote(am.group(1))
                    title = htmllib.unescape(re.sub(r"<[^>]+>", "", am.group(2))).strip()
                else:
                    continue
            else:
                href = am.group(1)
                title = htmllib.unescape(re.sub(r"<[^>]+>", "", am.group(2))).strip()
                if "uddg=" in href:
                    qs = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
                    href = urllib.parse.unquote(qs.get("uddg", [href])[0])
            if not title or not href.startswith("http"):
                continue
            snip_m = re.search(
                r'class="result__snippet"[^>]*>(.*?)</(?:a|td|div|span)>',
                block,
                re.I | re.S,
            )
            snippet = ""
            if snip_m:
                snippet = re.sub(r"<[^>]+>", "", snip_m.group(1)).strip()
                snippet = htmllib.unescape(re.sub(r"\s+", " ", snippet))
            results.append({"title": title, "url": href, "snippet": snippet})
            if len(results) >= limit:
                break

        if results:
            return results

        for m in re.finditer(
            r'uddg=([^&"]+).*?class="result__a"[^>]*>(.*?)</a>',
            html,
            re.I | re.S,
        ):
            link = urllib.parse.unquote(m.group(1))
            title = htmllib.unescape(re.sub(r"<[^>]+>", "", m.group(2))).strip()
            if title and link.startswith("http"):
                results.append({"title": title, "url": link, "snippet": ""})
            if len(results) >= limit:
                break
        if results:
            return results

        for m in re.finditer(
            r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
            html,
            re.I | re.S,
        ):
            href = m.group(1)
            title = htmllib.unescape(re.sub(r"<[^>]+>", "", m.group(2))).strip()
            if "uddg=" in href:
                qs = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
                href = urllib.parse.unquote(qs.get("uddg", [href])[0])
            if title and href.startswith("http"):
                results.append({"title": title, "url": href, "snippet": ""})
            if len(results) >= limit:
                break
        return results
