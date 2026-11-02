from __future__ import annotations
import re
import json
import html as htmllib
import base64
import urllib.parse
import urllib.request
from typing import Any


class YouTubeSearch:
    """Live YouTube-Treffer — primär ytInitialData, Fallback DDG/Bing, sonst Browser.

    Nie erfundene Videotitel. Bei Fehlschlag: BLOCKED (optional Browser-Suche öffnen).
    """

    _WATCH_RE = re.compile(
        r"(?:https?://)?(?:www\.)?(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/)([A-Za-z0-9_-]{6,})",
        re.I,
    )
    _INITIAL_RE = re.compile(
        r"var ytInitialData\s*=\s*(\{.+?\});</script>",
        re.S,
    )

    def search(
        self,
        query: str,
        limit: int = 6,
        *,
        open_browser: bool = False,
        play_first: bool = False,
        os_actions: Any = None,
    ) -> dict:
        q = (query or "").strip()
        q = re.sub(r"^(?:auf\s+|bei\s+)?(?:youtube|yt)\s*[:\-]?\s*", "", q, flags=re.I).strip()
        q = re.sub(r"^(?:suche|search|finde|videos?|clips?)\s*[:\-]?\s*", "", q, flags=re.I).strip() or q
        if not q:
            return {
                "success": False,
                "blocked": True,
                "error": "leere Query",
                "results": [],
                "reply": "BLOCKED: Keine YouTube-Suchanfrage — wonach soll ich suchen?",
            }

        search_page = "https://www.youtube.com/results?search_query=" + urllib.parse.quote_plus(q)
        results = self._live_youtube(q, limit, search_page)
        opened = None
        played = None

        # PLAYBACK first: watch?v=… for real playback when requested
        if results and play_first and os_actions is not None:
            watch = (results[0].get("url") or "").strip()
            if watch:
                try:
                    played = os_actions.open_url(watch)
                except Exception as e:
                    played = {"success": False, "error": str(e)}

        if open_browser and os_actions is not None and not (played and played.get("success")):
            try:
                opened = os_actions.open_url(search_page)
            except Exception as e:
                opened = {"success": False, "error": str(e)}

        if not results:
            if os_actions is not None and not (opened and opened.get("success")):
                try:
                    opened = os_actions.open_url(search_page)
                except Exception as e:
                    opened = {"success": False, "error": str(e)}
            if opened and opened.get("success"):
                return {
                    "success": True,
                    "blocked": False,
                    "results": [],
                    "opened_search": True,
                    "search_url": search_page,
                    "query": q,
                    "data": {"os": opened},
                    "reply": (
                        f"YouTube-Suche im Browser geöffnet (keine geparsten Treffer — "
                        f"keine erfundenen Titel):\n{search_page}"
                    ),
                }
            return {
                "success": False,
                "blocked": True,
                "error": "no_results_or_parse",
                "results": [],
                "search_url": search_page,
                "reply": (
                    f"BLOCKED: Keine verwertbaren YouTube-Treffer für „{q}“ "
                    "(Parser leer / Netz). Keine erfundenen Videotitel. "
                    f"Manuell: {search_page}"
                ),
            }

        lines = [
            "YouTube (Live-Treffer — nur geparste Titel/URLs, nichts erfunden):",
            f"Query: {q}",
            "",
        ]
        for i, r in enumerate(results, 1):
            lines.append(f"{i}. {r['title']}")
            lines.append(f"   {r['url']}")
        lines.append("")
        lines.append(f"Suchseite: {search_page}")
        if played and played.get("success"):
            lines.append("")
            lines.append(f"PLAYBACK geoeffnet: {results[0].get('url')}")
        if opened and opened.get("success"):
            lines.append("Browser-Suche zusätzlich geöffnet.")
        return {
            "success": True,
            "blocked": False,
            "results": results,
            "search_url": search_page,
            "opened_search": bool(opened and opened.get("success")),
            "played": bool(played and played.get("success")),
            "played_url": (results[0].get("url") if results and played and played.get("success") else None),
            "query": q,
            "data": {"os": opened, "play": played} if (opened or played) else {},
            "reply": "\n".join(lines),
        }


    def play(self, query: str, *, os_actions: Any = None, limit: int = 6) -> dict:
        """Suche + oeffne ersten Watch-URL zur echten Wiedergabe."""
        return self.search(query, limit=limit, play_first=True, os_actions=os_actions)
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

    def _fetch(self, url: str) -> str | None:
        try:
            req = urllib.request.Request(url, headers=self._headers())
            with urllib.request.urlopen(req, timeout=20) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except Exception:
            return None

    def _is_bot_wall(self, html: str) -> bool:
        low = (html or "").lower()
        if "anomaly-modal" in low or "bot challenge" in low:
            return True
        if "result__a" not in low and "uddg=" not in low and ("anomaly" in low or "sorry" in low):
            return True
        return False

    def _live_youtube(self, q: str, limit: int, search_page: str) -> list[dict]:
        # 1) Official YouTube results HTML (ytInitialData) — real titles
        html = self._fetch(search_page)
        results = self._parse_yt_initial(html, limit) if html else []
        if results:
            return results

        # 2) DuckDuckGo
        results = self._from_ddg(q, limit)
        if len(results) >= max(2, limit // 2):
            return results[:limit]

        # 3) Bing (often channels only — still take watch URLs if present)
        extra = self._from_bing(q, limit)
        seen = {r["url"] for r in results}
        for r in extra:
            if r["url"] not in seen:
                results.append(r)
                seen.add(r["url"])
            if len(results) >= limit:
                break
        return results[:limit]

    def _parse_yt_initial(self, html: str, limit: int) -> list[dict]:
        if not html or "ytInitialData" not in html:
            return []
        m = self._INITIAL_RE.search(html)
        if not m:
            m = re.search(r"ytInitialData\s*=\s*(\{.+?\});\s*(?:var|window)", html, re.S)
        if not m:
            return []
        try:
            data = json.loads(m.group(1))
        except Exception:
            return []

        found: list[dict] = []
        seen: set[str] = set()

        def walk(o: Any) -> None:
            if len(found) >= limit:
                return
            if isinstance(o, dict):
                vr = o.get("videoRenderer") or o.get("compactVideoRenderer")
                if isinstance(vr, dict):
                    vid = vr.get("videoId")
                    title = None
                    t = vr.get("title") or {}
                    if isinstance(t, dict):
                        runs = t.get("runs")
                        if isinstance(runs, list) and runs:
                            title = runs[0].get("text")
                        title = title or t.get("simpleText")
                    if vid and title:
                        watch = f"https://www.youtube.com/watch?v={vid}"
                        if watch not in seen:
                            seen.add(watch)
                            found.append({
                                "title": str(title).strip(),
                                "url": watch,
                                "video_id": vid,
                            })
                for v in o.values():
                    walk(v)
            elif isinstance(o, list):
                for v in o:
                    walk(v)

        walk(data)
        if found:
            return found[:limit]

        # Regex fallback inside page JSON
        for m2 in re.finditer(
            r'"videoId":"([A-Za-z0-9_-]{11})".{0,400}?"text":"([^"\\]{3,120})"',
            html,
            re.S,
        ):
            vid, title = m2.group(1), htmllib.unescape(m2.group(2))
            watch = f"https://www.youtube.com/watch?v={vid}"
            if watch in seen:
                continue
            seen.add(watch)
            found.append({"title": title, "url": watch, "video_id": vid})
            if len(found) >= limit:
                break
        return found[:limit]

    def _from_ddg(self, q: str, limit: int) -> list[dict]:
        out: list[dict] = []
        for ddg_q in (f"site:youtube.com/watch {q}", f"site:youtube.com {q}"):
            html = self._fetch("https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(ddg_q))
            if not html or self._is_bot_wall(html):
                continue
            for r in self._parse_generic(html, limit):
                if r["url"] not in {x["url"] for x in out}:
                    out.append(r)
                if len(out) >= limit:
                    return out
        return out

    def _from_bing(self, q: str, limit: int) -> list[dict]:
        html = self._fetch(
            "https://www.bing.com/search?q="
            + urllib.parse.quote(f"site:youtube.com/watch {q}")
            + "&setlang=de-DE"
        )
        if not html:
            return []
        return self._parse_bing(html, limit)

    def _normalize_watch(self, href: str) -> tuple[str, str] | None:
        href = (href or "").strip()
        if "uddg=" in href:
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
            href = urllib.parse.unquote(qs.get("uddg", [href])[0])
        m = self._WATCH_RE.search(href)
        if not m:
            return None
        vid = m.group(1)
        return vid, f"https://www.youtube.com/watch?v={vid}"

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
        if href.startswith("http") and "bing.com/ck" not in href:
            return href
        if cite_txt:
            c = cite_txt.replace(" › ", "/").split()[0].strip()
            if c.startswith("http"):
                return c
        return None

    def _add_watch(self, results: list[dict], seen: set[str], title: str, href: str) -> None:
        title = htmllib.unescape(re.sub(r"<[^>]+>", "", title or "")).strip()
        title = re.sub(r"\s+", " ", title)
        norm = self._normalize_watch(href)
        if not norm or not title:
            return
        vid, watch = norm
        if watch in seen:
            return
        if title.lower() in ("youtube", "www.youtube.com", "youtube.com"):
            return
        seen.add(watch)
        results.append({"title": title, "url": watch, "video_id": vid})

    def _parse_generic(self, html: str, limit: int) -> list[dict]:
        results: list[dict] = []
        seen: set[str] = set()
        for m in re.finditer(
            r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
            html,
            re.I | re.S,
        ):
            self._add_watch(results, seen, m.group(2), m.group(1))
            if len(results) >= limit:
                break
        return results[:limit]

    def _parse_bing(self, html: str, limit: int) -> list[dict]:
        results: list[dict] = []
        seen: set[str] = set()
        blocks = re.split(r'class="b_algo"', html, flags=re.I)
        for block in blocks[1:]:
            am = re.search(
                r'<h2[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
                block,
                re.I | re.S,
            )
            if not am:
                continue
            cite_m = re.search(r"<cite[^>]*>(.*?)</cite>", block, re.I | re.S)
            cite_txt = (
                htmllib.unescape(re.sub(r"<[^>]+>", "", cite_m.group(1))).strip()
                if cite_m
                else ""
            )
            dest = self._bing_dest(am.group(1), cite_txt) or ""
            self._add_watch(results, seen, am.group(2), dest or cite_txt)
            if len(results) >= limit:
                break
        return results[:limit]
