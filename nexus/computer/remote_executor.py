"""Bounded PC music executor; only observed effects count as success.

The caller owns authentication, durable command IDs and spoken confirmation.
This module never accepts shell commands, executable paths or browser scripts.
It controls one visible browser with its own profile/cache/temp on the workspace
drive. It never attaches to the user's browser or accepts YouTube consent.
"""
from __future__ import annotations

import os
import platform
import re
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from nexus.common.paths import WORKSPACE

APPS = frozenset({"browser", "youtube", "spotify", "notepad", "calc"})
INTENTS = frozenset({"app.open", "url.open", "youtube.search", "media.play_search",
                     "media.play", "media.resume", "media.pause", "media.next", "media.prev",
                     "media.stop", "volume.set", "volume.up", "volume.down", "volume.mute", "pc.status"})
HOSTS = frozenset({"youtube.com", "www.youtube.com", "open.spotify.com"})
YOUTUBE = "https://www.youtube.com/"
SPOTIFY = "https://open.spotify.com/"
VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


class ActionError(ValueError):
    """A stable, non-secret failure code safe to return to the phone."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _url(raw: object) -> str:
    if type(raw) is not str or len(raw) > 500 or any(ord(c) < 32 for c in raw):
        raise ActionError("invalid_url")
    try:
        u = urlsplit(raw)
        if (u.scheme != "https" or u.hostname not in HOSTS or u.username or u.password
                or u.port not in (None, 443) or u.fragment):
            raise ActionError("url_not_allowed")
    except ValueError as exc:
        raise ActionError("url_not_allowed") from exc
    if u.hostname in {"youtube.com", "www.youtube.com"}:
        if u.path not in ("", "/", "/watch", "/results"):
            raise ActionError("url_path_not_allowed")
        params = parse_qs(u.query, keep_blank_values=True)
        if u.path == "/watch":
            if set(params) - {"v", "list", "index"} or len(params.get("v", [])) != 1 or not VIDEO_ID.fullmatch(params["v"][0]):
                raise ActionError("invalid_youtube_url")
        elif u.path == "/results":
            if set(params) != {"search_query"} or len(params["search_query"]) != 1:
                raise ActionError("invalid_youtube_url")
        elif u.query:
            raise ActionError("url_query_not_allowed")
        return urlunsplit(("https", "www.youtube.com", u.path or "/", u.query, ""))
    if u.query or not re.fullmatch(r"/(?:|(?:intl-[a-z]{2}/)?(?:track|album|artist|playlist)/[A-Za-z0-9]+)", u.path or "/"):
        raise ActionError("url_path_not_allowed")
    return urlunsplit(("https", "open.spotify.com", u.path or "/", "", ""))


def validate(intent: str, args: dict | None = None) -> dict:
    """Return normalized arguments or raise ActionError; unknown fields fail closed."""
    if type(intent) is not str or intent not in INTENTS:
        raise ActionError("intent_not_allowed")
    if args is None:
        args = {}
    if type(args) is not dict:
        raise ActionError("invalid_args")
    fields = ({"app"} if intent == "app.open" else {"url"} if intent == "url.open" else
              {"query", "target"} if intent in {"youtube.search", "media.play_search"} else
              {"percent", "target"} if intent == "volume.set" else
              {"target"} if intent.startswith(("media.", "volume.")) else set())
    if set(args) - fields:
        raise ActionError("unexpected_args")
    out = dict(args)
    if "target" in args and args["target"] != "youtube":
        raise ActionError("target_not_allowed")
    if intent == "app.open":
        if type(args.get("app")) is not str or args["app"] not in APPS:
            raise ActionError("app_not_allowed")
    elif intent == "url.open":
        out["url"] = _url(args.get("url"))
    elif intent in {"youtube.search", "media.play_search"}:
        q = args.get("query")
        if type(q) is not str or not q.strip() or len(q) > 200 or any(ord(c) < 32 for c in q):
            raise ActionError("invalid_query")
        out["query"] = q.strip()
        out["target"] = "youtube"
    elif intent == "volume.set":
        if type(args.get("percent")) is not int or not 0 <= args["percent"] <= 100:
            raise ActionError("invalid_percent")
        out["target"] = "youtube"
    elif intent.startswith(("media.", "volume.")):
        out["target"] = "youtube"
    return out


_SNAPSHOT = r"""
const v=document.querySelector('video'), p=document.querySelector('#movie_player');
const u=new URL(location.href), e=document.querySelector('#reason, .yt-playability-error-supported-renderers');
let observedId=null,observedTitle=null;try{const d=p&&p.getVideoData?p.getVideoData():null;
 observedId=d?d.video_id:null;observedTitle=d?d.title:null;}catch(_){}
return {url:location.href,title:document.title,host:location.hostname,ready:document.readyState,
 id:u.searchParams.get('v'),observed_id:observedId,observed_title:observedTitle,consent:location.hostname==='consent.youtube.com'||
 !!document.querySelector('ytd-consent-bump-v2-lightbox, ytd-consent-bump-lightbox'),
 player_error:e?(e.innerText||'').slice(0,180):null,
 video: v?{paused:v.paused,ended:v.ended,time:v.currentTime,ready:v.readyState,
 volume:v.volume,muted:v.muted,error:v.error?v.error.code:null,
 ad:!!(p&&p.classList.contains('ad-showing'))}:null};
"""
_RESULTS = r"""
if(location.pathname!=='/results'||new URL(location.href).searchParams.get('search_query')!==arguments[0])return [];
return [...document.querySelectorAll('ytd-video-renderer a#video-title')].map(a=>{
 const u=new URL(a.href),id=u.searchParams.get('v');
 if(u.hostname!=='www.youtube.com'||u.pathname!=='/watch'||!id||!/^[A-Za-z0-9_-]{11}$/.test(id))return null;
 return {title:(a.textContent||'').trim(),url:'https://www.youtube.com/watch?v='+id};
}).filter(x=>x&&x.title).slice(0,8);
"""
_PLAY = r"""
const done=arguments[arguments.length-1],v=document.querySelector('video');
if(!v){done({ok:false,reason:'video_missing'});return;}
v.play().then(()=>done({ok:true})).catch(()=>done({ok:false,reason:'playback_not_permitted'}));
"""
_PAUSE = "const v=document.querySelector('video');if(v){v.pause();}return !!v;"
_STOP = "const v=document.querySelector('video');if(v){v.pause();v.currentTime=0;}return !!v;"
_VOLUME = "const v=document.querySelector('video');if(v){v.volume=arguments[0];v.muted=arguments[1];}return !!v;"
_NEXT = "const b=document.querySelector('.ytp-next-button');if(!b||b.getAttribute('aria-disabled')==='true')return false;b.click();return true;"


def _brave_binary() -> Path:
    """Fixed installed Brave locations are read-only inputs, never destinations."""
    browsers = [Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "BraveSoftware/Brave-Browser/Application/brave.exe",
                Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "BraveSoftware/Brave-Browser/Application/brave.exe"]
    if os.environ.get("LOCALAPPDATA"):
        browsers.append(Path(os.environ["LOCALAPPDATA"]) / "BraveSoftware/Brave-Browser/Application/brave.exe")
    binary = next((p for p in browsers if p.is_file()), None)
    if binary is None:
        raise ActionError("brave_not_installed")
    return binary


def _browser_major_version(binary: Path) -> int:
    """Read the Windows file version without launching Brave or a shell."""
    import ctypes
    from ctypes import wintypes

    version = ctypes.WinDLL("version", use_last_error=True)
    version.GetFileVersionInfoSizeW.argtypes = (wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD))
    version.GetFileVersionInfoSizeW.restype = wintypes.DWORD
    version.GetFileVersionInfoW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID)
    version.GetFileVersionInfoW.restype = wintypes.BOOL
    version.VerQueryValueW.argtypes = (wintypes.LPCVOID, wintypes.LPCWSTR,
                                      ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.UINT))
    version.VerQueryValueW.restype = wintypes.BOOL
    size = version.GetFileVersionInfoSizeW(str(binary), None)
    if not size:
        raise ActionError("brave_version_unavailable")
    data = ctypes.create_string_buffer(size)
    pointer, length = ctypes.c_void_p(), wintypes.UINT()
    if (not version.GetFileVersionInfoW(str(binary), 0, size, data)
            or not version.VerQueryValueW(data, "\\", ctypes.byref(pointer), ctypes.byref(length))
            or length.value < 13 * ctypes.sizeof(wintypes.DWORD)):
        raise ActionError("brave_version_unavailable")
    fixed = ctypes.cast(pointer, ctypes.POINTER(wintypes.DWORD * 13)).contents
    if fixed[0] != 0xFEEF04BD or not fixed[2] >> 16:
        raise ActionError("brave_version_unavailable")
    return fixed[2] >> 16


def _chromedriver_path(binary: Path) -> Path:
    major = _browser_major_version(binary)
    drivers = sorted((WORKSPACE / "cache/selenium/chromedriver/win64").glob("*/chromedriver.exe"),
                     key=lambda p: tuple(int(n) for n in p.parent.name.split(".") if n.isdigit()), reverse=True)
    driver = next((p for p in drivers if p.is_file() and p.parent.name.split(".")[0] == str(major)), None)
    if driver is None:
        raise ActionError("matching_brave_chromedriver_missing")
    if driver.resolve().drive.upper() != "G:":
        raise ActionError("driver_must_be_on_g")
    return driver


def _driver_factory(root: Path):
    """Use installed Brave and a matching G: driver, without Selenium Manager."""
    if os.name != "nt":
        raise ActionError("windows_browser_required")
    if root.resolve().drive.upper() != "G:":
        raise ActionError("browser_storage_must_be_on_g")
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.chrome.service import Service
    except ImportError as exc:
        raise ActionError("selenium_not_installed") from exc
    binary = _brave_binary()
    driver_path = _chromedriver_path(binary)
    folders = ("profile", "cache", "temp", "crash", "downloads", "appdata", "localappdata")
    for name in (*folders, "browser.log", "webdriver.log"):
        # Existing reparse points must not redirect a profile, log or cache to C:.
        if (root / name).resolve().drive.upper() != "G:":
            raise ActionError("browser_storage_must_be_on_g")
    for folder in folders:
        (root / folder).mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.pop("SE_CHROMEDRIVER", None)
    env.update(TEMP=str(root / "temp"), TMP=str(root / "temp"), TMPDIR=str(root / "temp"),
               APPDATA=str(root / "appdata"), LOCALAPPDATA=str(root / "localappdata"),
               SE_CACHE_PATH=str(WORKSPACE / "cache/selenium"), PYTHONDONTWRITEBYTECODE="1")
    options = Options()
    options.binary_location = str(binary)
    for arg in ("--user-data-dir=" + str(root / "profile"), "--disk-cache-dir=" + str(root / "cache"),
                "--crash-dumps-dir=" + str(root / "crash"), "--log-file=" + str(root / "browser.log"),
                "--no-first-run", "--no-default-browser-check", "--disable-sync", "--disable-background-networking",
                "--disable-component-update", "--disable-breakpad", "--disable-crash-reporter",
                "--disable-features=MediaRouter,OptimizationHints"):
        options.add_argument(arg)
    options.add_experimental_option("prefs", {"download.default_directory": str(root / "downloads"),
                                             "credentials_enable_service": False,
                                             "profile.password_manager_enabled": False})
    class FixedChromeService(Service):
        # Selenium also consults the parent's global SE_CHROMEDRIVER when Chrome
        # starts. Pin the validated path without changing global process state.
        def env_path(self):
            return None

    # Service env is inherited by the browser; do not mutate global process env.
    service = FixedChromeService(executable_path=str(driver_path), log_output=str(root / "webdriver.log"), env=env)
    service.creation_flags = 0x08000000  # Hide helper console; browser stays visible.
    driver = webdriver.Chrome(service=service, options=options)
    try:
        driver.set_page_load_timeout(35)
        driver.set_script_timeout(8)
        driver.set_window_size(1280, 900)
    except Exception:
        try:
            driver.quit()
        except Exception:
            pass
        raise
    return driver


class RemoteExecutor:
    """One serialized action and one controlled player; no background shell."""

    def __init__(self, *, browser_factory=None, browser_root: Path | None = None,
                 timeout: float = 18.0):
        self._factory = browser_factory or _driver_factory
        self._root = browser_root or (WORKSPACE / "pc_remote/brave")
        self._driver = None
        self._lock = threading.Lock()
        self._timeout = timeout
        self._history: list[str] = []
        self._history_index = -1
        self._context_lost = False

    def _browser(self):
        if self._driver is None:
            self._driver = self._factory(self._root)
        return self._driver

    def _discard_browser(self) -> None:
        previous, self._driver = self._driver, None
        self._history.clear()
        self._history_index = -1
        self._context_lost = True
        if previous is not None:
            try:
                previous.quit()
            except Exception:
                pass

    def _prepare_browser(self, intent: str, args: dict) -> None:
        # Probe only before any action. Recovery inside observation helpers could
        # replay next/play/volume after their effect but before its verification.
        if (intent == "pc.status"
                or (intent == "app.open" and args["app"] in {"notepad", "calc"})):
            return
        if self._driver is not None:
            try:
                alive = bool(self._driver.window_handles)
            except Exception:
                alive = False
            if not alive:
                self._discard_browser()
                self._driver = self._factory(self._root)
        if self._context_lost and intent.startswith(("media.", "volume.")) and intent != "media.play_search":
            raise ActionError("browser_context_lost")

    def _state(self) -> dict:
        s = self._browser().execute_script(_SNAPSHOT)
        if type(s) is not dict:
            raise ActionError("browser_observation_missing")
        if s.get("consent"):
            raise ActionError("youtube_consent_required_on_pc")
        if s.get("host") not in HOSTS:
            raise ActionError("browser_left_allowed_site")
        return s

    def _wait(self, predicate, failure: str, timeout: float | None = None) -> dict:
        until = time.monotonic() + (self._timeout if timeout is None else timeout)
        while True:
            s = self._state()
            if s.get("player_error"):
                raise ActionError("youtube_player_unavailable")
            if predicate(s):
                return s
            if time.monotonic() >= until:
                raise ActionError(failure)
            time.sleep(0.2)

    def _navigate(self, url: str) -> dict:
        url = _url(url)
        self._browser().get(url)
        wanted = urlsplit(url)
        expected_query = parse_qs(wanted.query, keep_blank_values=True)

        def arrived(s):
            observed = urlsplit(s.get("url", ""))
            observed_query = parse_qs(observed.query, keep_blank_values=True)
            return (s.get("host") == wanted.hostname and s.get("ready") == "complete"
                    and observed.path.rstrip("/") == wanted.path.rstrip("/")
                    and all(observed_query.get(k) == values for k, values in expected_query.items()))

        state = self._wait(arrived, "page_load_unverified")
        self._context_lost = False
        return state

    def _video(self) -> dict:
        s = self._wait(lambda x: bool(x.get("video")) and x["video"].get("ready", 0) >= 2
                       and x.get("observed_id") == x.get("id"),
                       "youtube_video_not_ready")
        if s.get("host") != "www.youtube.com" or not VIDEO_ID.fullmatch(str(s.get("id") or "")):
            raise ActionError("controlled_youtube_video_missing")
        if s["video"].get("error"):
            raise ActionError("youtube_video_error")
        if s["video"].get("ad"):
            raise ActionError("youtube_ad_active")
        return s

    def _playing(self) -> dict:
        before = self._video()
        if before["video"].get("muted") or before["video"].get("volume", 0) <= 0:
            raise ActionError("youtube_player_muted")
        result = self._browser().execute_async_script(_PLAY)
        if not isinstance(result, dict) or result.get("ok") is not True:
            raise ActionError("playback_not_permitted")
        def advancing(s):
            v = s.get("video") or {}
            return (s.get("id") == before.get("id") and s.get("observed_id") == s.get("id") and v.get("paused") is False
                    and not v.get("ended") and not v.get("ad") and not v.get("error")
                    and v.get("time", 0) > before["video"].get("time", 0) + 0.15)
        return self._wait(advancing, "playback_progress_unverified", timeout=min(self._timeout, 8.0))

    def _remember(self, s: dict) -> None:
        url = YOUTUBE + "watch?v=" + str(s["id"])
        if self._history_index >= 0 and self._history[self._history_index] == url:
            return
        self._history = self._history[:self._history_index + 1] + [url]
        self._history = self._history[-50:]
        self._history_index = len(self._history) - 1

    def _search(self, query: str) -> tuple[dict, list[dict]]:
        self._navigate(YOUTUBE + "results?" + urlencode({"search_query": query}))
        until = time.monotonic() + self._timeout
        while True:
            state = self._state()  # Consent / redirects remain fail-closed while results load.
            if parse_qs(urlsplit(state.get("url", "")).query).get("search_query") != [query]:
                raise ActionError("youtube_search_navigation_changed")
            found = self._browser().execute_script(_RESULTS, query)
            results = []
            for item in found if isinstance(found, list) else []:
                if not isinstance(item, dict):
                    continue
                try:
                    url = _url(item.get("url"))
                    if urlsplit(url).path == "/watch" and type(item.get("title")) is str:
                        results.append({"url": url, "title": item["title"][:200]})
                except ActionError:
                    continue
            if results:
                return state, results
            if time.monotonic() >= until:
                raise ActionError("youtube_search_results_unavailable")
            time.sleep(0.25)

    def execute(self, intent: str, args: dict | None = None) -> dict:
        """Execute one validated operation. Failure never contains verified=True."""
        try:
            clean = validate(intent, args)
        except ActionError as exc:
            return self._failed(exc.code)
        with self._lock:
            try:
                self._prepare_browser(intent, clean)
                return self._execute(intent, clean)
            except ActionError as exc:
                return self._failed(exc.code)
            except Exception as exc:
                # Browser/service exceptions can contain credentials, file paths or raw HTML.
                return self._failed("browser_executor_" + type(exc).__name__)

    @staticmethod
    def _failed(code: str) -> dict:
        return {"success": False, "error": code,
                "reply": "Die PC-Aktion ist blockiert: " + code + ". Erfolg ist nicht nachgewiesen.",
                "evidence": {"verified": False, "backend": "controlled_pc_browser"}}

    @staticmethod
    def _done(reply: str, **evidence) -> dict:
        return {"success": True, "reply": reply, "error": None,
                "evidence": {"verified": True, "backend": "controlled_pc_browser", **evidence}}

    def _execute(self, intent: str, args: dict) -> dict:
        if intent == "pc.status":
            try:
                import psutil
                cpu, memory = psutil.cpu_percent(interval=0.1), psutil.virtual_memory()
                uptime = max(0, int(time.time() - psutil.boot_time()))
            except ImportError as exc:
                raise ActionError("pc_status_dependency_missing") from exc
            return self._done(f"Der PC läuft. Prozessor {round(cpu)} Prozent, Arbeitsspeicher {round(memory.percent)} Prozent.",
                              backend="local_system_readback", host=platform.node(), cpu_percent=cpu,
                              ram_percent=memory.percent, uptime_seconds=uptime)
        if intent == "app.open":
            app = args["app"]
            if app in {"notepad", "calc"}:
                raise ActionError("native_apps_storage_uncontrolled")
            url = SPOTIFY if app == "spotify" else YOUTUBE
            state = self._navigate(url)
            return self._done(f"{'Spotify' if app == 'spotify' else 'YouTube'} ist im kontrollierten PC-Browser geöffnet.",
                              action=intent, app=app, state=state)
        if intent == "url.open":
            return self._done("Die freigegebene Musikseite ist im PC-Browser geöffnet.",
                              action=intent, state=self._navigate(args["url"]))
        if intent in {"youtube.search", "media.play_search"}:
            state, results = self._search(args["query"])
            if intent == "youtube.search":
                return self._done("Die YouTube-Suchergebnisse sind am PC geöffnet.", action=intent,
                                  query=args["query"], state=state, results=results)
            self._navigate(results[0]["url"])
            played = self._playing()
            self._remember(played)
            return self._done(f"{results[0]['title']} läuft auf YouTube am PC.", action=intent,
                              query=args["query"], selected=results[0], state=played)
        before = self._video()
        if intent in {"media.play", "media.resume"}:
            after = self._playing()
            self._remember(after)
            return self._done("YouTube am PC spielt weiter.", action=intent, before=before, after=after)
        if intent in {"media.pause", "media.stop"}:
            self._browser().execute_script(_STOP if intent == "media.stop" else _PAUSE)
            after = self._wait(lambda s: s.get("id") == before["id"] and bool(s.get("video"))
                               and s["video"].get("paused") is True
                               and (intent != "media.stop" or s["video"].get("time", 1) <= 0.5),
                               "pause_or_stop_unverified")
            return self._done("YouTube am PC ist gestoppt." if intent == "media.stop" else "YouTube am PC ist pausiert.",
                              action=intent, before=before, after=after)
        if intent == "media.next":
            self._remember(before)
            if not self._browser().execute_script(_NEXT):
                raise ActionError("next_track_unavailable")
            self._wait(lambda s: s.get("id") != before["id"] and VIDEO_ID.fullmatch(str(s.get("id") or "")),
                       "next_track_change_unverified")
            after = self._playing()
            self._remember(after)
            return self._done("Der nächste YouTube-Titel läuft am PC.", action=intent, before=before, after=after)
        if intent == "media.prev":
            if self._history_index <= 0:
                raise ActionError("previous_track_unavailable")
            target_index = self._history_index - 1
            self._navigate(self._history[target_index])
            after = self._playing()
            if after["id"] == before["id"]:
                raise ActionError("previous_track_change_unverified")
            self._history_index = target_index
            return self._done("Der vorherige YouTube-Titel läuft am PC.", action=intent, before=before, after=after)
        if intent.startswith("volume."):
            v = before["video"]
            wanted = (args["percent"] / 100 if intent == "volume.set" else
                      min(1.0, v["volume"] + 0.1) if intent == "volume.up" else
                      max(0.0, v["volume"] - 0.1) if intent == "volume.down" else v["volume"])
            muted = intent == "volume.mute"
            self._browser().execute_script(_VOLUME, wanted, muted)
            after = self._wait(lambda s: s.get("id") == before["id"] and bool(s.get("video"))
                               and abs(s["video"].get("volume", -1) - wanted) < 0.005
                               and s["video"].get("muted") is muted,
                               "youtube_volume_unverified")
            reply = "YouTube am PC ist stumm." if muted else f"YouTube am PC hat {round(wanted * 100)} Prozent Lautstärke."
            return self._done(reply, action=intent, scope="controlled_youtube_player", before=before, after=after)
        raise ActionError("intent_not_allowed")


_EXECUTOR: RemoteExecutor | None = None
_INIT_LOCK = threading.Lock()


def execute(intent: str, args: dict | None = None) -> dict:
    global _EXECUTOR
    if intent == "media.play_search" and _local_fallback_enabled() and not _has_query(args):
        return _local_player("no_youtube_query")
    with _INIT_LOCK:
        if _EXECUTOR is None:
            _EXECUTOR = RemoteExecutor()
    result = _EXECUTOR.execute(intent, args)
    if (intent == "media.play_search" and _local_fallback_enabled()
            and result.get("error") in LOCAL_FALLBACK_ERRORS):
        return _local_player(result["error"])
    return result


# --- Phase 2 Schritt 7c (02.11.2026, Eddie 13:58): lokaler Player als Ausweichweg ---
# Kein Warten auf Werbung, kein Klick. YouTube-Pruefung und Consent bleiben unveraendert;
# nur deren Fehlercode loest den Ausweichweg aus. NEXUS_PC_MUSIC_LOCAL_FALLBACK=0 = altes Verhalten.
LOCAL_FALLBACK_ERRORS = frozenset({"youtube_ad_active", "youtube_consent_required_on_pc"})
LOCAL_PLAYER_URL = "http://127.0.0.1:8299/api/media/play_at"


def _local_fallback_enabled() -> bool:
    return os.environ.get("NEXUS_PC_MUSIC_LOCAL_FALLBACK", "1").strip() != "0"


def _has_query(args) -> bool:
    return isinstance(args, dict) and isinstance(args.get("query"), str) and bool(args["query"].strip())


def _in_process_media():
    import sys
    routes = sys.modules.get("nexus.chat.routes")
    engine = getattr(routes, "ENGINE", None) if routes is not None else None
    master = getattr(engine, "master", None)
    return getattr(master, "media", None)


def _http_play_at(index: int) -> dict:
    import json
    import urllib.request
    req = urllib.request.Request(LOCAL_PLAYER_URL, data=json.dumps({"index": index}).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data if isinstance(data, dict) else {}


def _local_player(reason: str) -> dict:
    media = _in_process_media()
    via = "in_process" if media is not None else "http_loopback"
    try:
        raw = media.play_at(0) if media is not None else _http_play_at(0)
    except Exception as exc:
        raw = {"success": False, "error": "local_player_" + type(exc).__name__}
    raw = raw if isinstance(raw, dict) else {}
    state = raw.get("state") if isinstance(raw.get("state"), dict) else {}
    cur = state.get("current") if isinstance(state.get("current"), dict) else {}
    path = cur.get("path")
    ok = raw.get("success") is True and bool(path)
    evidence = {"verified": ok, "backend": "local_player", "fallback_reason": reason, "via": via}
    if ok:
        evidence.update(track=cur.get("name"), path=path, index=state.get("index"),
                        playing=state.get("playing"), count=state.get("count"))
        return {"success": True, "error": None, "reply": f"Spiele lokal: {cur.get('name')}",
                "evidence": evidence}
    code = str(raw.get("error") or "local_player_unverified")
    return {"success": False, "error": code,
            "reply": "Die PC-Aktion ist blockiert: " + code + ". Erfolg ist nicht nachgewiesen.",
            "evidence": evidence}
