"""Offline security and observed-outcome contracts. No browser or PC mutations."""
from __future__ import annotations

import copy
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from nexus.computer import remote_executor as remote


def state(video_id="dQw4w9WgXcQ", **video):
    return {"url": remote.YOUTUBE + "watch?v=" + video_id, "title": "Observed test video",
            "host": "www.youtube.com", "ready": "complete", "id": video_id,
            "observed_id": video_id, "consent": False, "player_error": None,
            "video": {"paused": False, "ended": False, "time": 10.0, "ready": 4,
                      "volume": 0.4, "muted": False, "error": None, "ad": False, **video}}


class Browser:
    """A controllable external observation, including ignored commands."""

    def __init__(self):
        self.snapshot = state()
        self.ignored = set()
        self.calls = []
        self.consent = False
        self.play_progress = True
        self.alive = True
        self.windows = ["controlled-test-window"]
        self.probes = 0
        self.quit_calls = 0
        self.quit_error = False

    @property
    def window_handles(self):
        self.probes += 1
        if not self.alive:
            raise RuntimeError("isolated stale browser session")
        return self.windows

    def quit(self):
        self.quit_calls += 1
        self.alive = False
        if self.quit_error:
            raise RuntimeError("isolated stale driver cleanup failure")

    def get(self, url):
        self.calls.append(("navigate", url))
        parts = urlsplit(url)
        vid = parse_qs(parts.query).get("v", [self.snapshot["id"]])[0]
        self.snapshot.update(url=url, host=parts.hostname, id=vid, observed_id=vid, consent=self.consent)

    def execute_script(self, script, *args):
        if script == remote._SNAPSHOT:
            if self.snapshot["video"]["paused"] is False and self.play_progress:
                self.snapshot["video"]["time"] += 0.25
            return copy.deepcopy(self.snapshot)
        self.calls.append(("script", script, args))
        if script in self.ignored:
            return True
        if script == remote._PAUSE:
            self.snapshot["video"]["paused"] = True
        elif script == remote._STOP:
            self.snapshot["video"].update(paused=True, time=0)
        elif script == remote._VOLUME:
            self.snapshot["video"].update(volume=args[0], muted=args[1])
        elif script == remote._RESULTS:
            return [{"title": "Real result", "url": remote.YOUTUBE + "watch?v=dQw4w9WgXcQ"},
                    {"title": "Injected destination", "url": "https://evil.invalid/"}]
        elif script == remote._NEXT:
            self.snapshot = state("9bZkp7q19f0")
        return True

    def execute_async_script(self, script):
        self.calls.append(("async", script))
        if script not in self.ignored:
            self.snapshot["video"]["paused"] = False
        return {"ok": True}


class Validation(unittest.TestCase):
    def test_unknown_actions_and_extra_args_are_rejected(self):
        for intent, args in (("shell", {"cmd": "calc"}), ("app.open", {"app": "powershell"}),
                             ("pc.status", {"command": "anything"}),
                             ("media.pause", {"script": "document.body.remove()"}),
                             ("media.pause", {"target": "system"})):
            with self.subTest(intent=intent, args=args), self.assertRaises(remote.ActionError):
                remote.validate(intent, args)

    def test_url_policy_blocks_redirects_schemes_credentials_ports(self):
        urls = ["file:///C:/secret", "https://youtube.com.evil.invalid/", "https://user@youtube.com/",
                "https://youtube.com:444/", "https://youtube.com/redirect?q=https://evil.invalid/",
                "https://open.spotify.com/?next=evil", "https://youtube.com/#script",
                "https://youtube.com/\n"]
        for url in urls:
            with self.subTest(url=url), self.assertRaises(remote.ActionError):
                remote.validate("url.open", {"url": url})

    def test_query_is_data_and_percent_is_bounded_integer(self):
        self.assertEqual(remote.validate("media.play_search", {"query": " AC/DC & Metal "}),
                         {"query": "AC/DC & Metal", "target": "youtube"})
        for value in (-1, 101, 2.5, "30", True, None):
            with self.subTest(value=value), self.assertRaises(remote.ActionError):
                remote.validate("volume.set", {"percent": value})
        self.assertEqual(remote.validate("volume.set", {"percent": 0})["percent"], 0)


@unittest.skipUnless(os.name == "nt", "Windows Brave factory contract")
class BraveStorage(unittest.TestCase):
    def test_brave_factory_uses_separate_g_storage_and_no_session_attachment(self):
        report_root = Path(__file__).resolve().parents[1] / "workspace/reports/pc_remote_20261022"
        self.assertEqual(report_root.resolve().drive.upper(), "G:")
        report_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="brave-factory-", dir=report_root) as directory:
            root = Path(directory) / "controlled-brave"
            binary = Path("C:/Program Files/BraveSoftware/Brave-Browser/Application/brave.exe")
            driver_path = Path(directory) / "chromedriver.exe"
            driver_path.write_bytes(b"isolated driver placeholder; never executed")
            driver = mock.Mock()
            with mock.patch.dict(os.environ, {"SE_CHROMEDRIVER": "C:/uncontrolled/chromedriver.exe"}), mock.patch.object(
                    remote, "_brave_binary", return_value=binary), mock.patch.object(
                    remote, "_chromedriver_path", return_value=driver_path) as find_driver, mock.patch(
                    "selenium.webdriver.Chrome", return_value=driver) as chrome:
                result = remote._driver_factory(root)
                configured_service = chrome.call_args.kwargs["service"]
                # Chromium WebDriver checks the process environment again before start.
                self.assertIsNone(configured_service.env_path())
                start_path = configured_service.env_path() or configured_service.path
                self.assertEqual(Path(start_path).resolve(), driver_path.resolve())

            self.assertIs(result, driver)
            find_driver.assert_called_once_with(binary)
            chrome.assert_called_once()
            options, service = chrome.call_args.kwargs["options"], chrome.call_args.kwargs["service"]
            self.assertEqual(options.binary_location, str(binary))
            self.assertEqual(Path(service.path).resolve(), driver_path.resolve())
            self.assertNotIn("SE_CHROMEDRIVER", service.env)
            self.assertIn("--user-data-dir=" + str(root / "profile"), options.arguments)
            self.assertIn("--disk-cache-dir=" + str(root / "cache"), options.arguments)
            self.assertIn("--crash-dumps-dir=" + str(root / "crash"), options.arguments)
            for key, folder in (("TEMP", "temp"), ("TMP", "temp"), ("TMPDIR", "temp"),
                                ("APPDATA", "appdata"), ("LOCALAPPDATA", "localappdata")):
                self.assertEqual(Path(service.env[key]).resolve(), (root / folder).resolve())
                self.assertEqual(Path(service.env[key]).resolve().drive.upper(), "G:")
            self.assertEqual(options.experimental_options["prefs"]["download.default_directory"],
                             str(root / "downloads"))
            self.assertFalse(any("debugger-address" in arg or "remote-debugging-port" in arg
                                 for arg in options.arguments))
            self.assertNotIn("debuggerAddress", options.experimental_options)
            driver.set_page_load_timeout.assert_called_once()
            driver.set_script_timeout.assert_called_once()

    def test_driver_setup_failure_closes_created_browser_and_preserves_primary_error(self):
        report_root = Path(__file__).resolve().parents[1] / "workspace/reports/pc_remote_20261022"
        self.assertEqual(report_root.resolve().drive.upper(), "G:")
        report_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="brave-setup-failure-", dir=report_root) as directory:
            binary = Path("C:/Program Files/BraveSoftware/Brave-Browser/Application/brave.exe")
            for method in ("set_page_load_timeout", "set_script_timeout", "set_window_size"):
                with self.subTest(method=method):
                    driver = mock.Mock()
                    getattr(driver, method).side_effect = ValueError("isolated browser setup failure")
                    driver.quit.side_effect = RuntimeError("isolated secondary cleanup failure")
                    with mock.patch.object(remote, "_brave_binary", return_value=binary), mock.patch.object(
                            remote, "_chromedriver_path", return_value=Path(directory) / "chromedriver.exe"), mock.patch(
                            "selenium.webdriver.Chrome", return_value=driver), self.assertRaisesRegex(
                            ValueError, "isolated browser setup failure"):
                        remote._driver_factory(Path(directory) / method)
                    driver.quit.assert_called_once_with()


class ObservedOutcomes(unittest.TestCase):
    def setUp(self):
        self.browser = Browser()
        self.starts = 0

        def factory(_root):
            self.starts += 1
            return self.browser

        self.executor = remote.RemoteExecutor(browser_factory=factory, timeout=0)

    def test_unknown_intent_never_starts_browser(self):
        result = self.executor.execute("shell", {"command": "whoami"})
        self.assertFalse(result["success"])
        self.assertFalse(result["evidence"]["verified"])
        self.assertEqual(self.starts, 0)

    def test_native_apps_blocked_before_c_storage_is_touched(self):
        for app in ("notepad", "calc"):
            result = self.executor.execute("app.open", {"app": app})
            self.assertEqual(result["error"], "native_apps_storage_uncontrolled")
        self.assertEqual(self.starts, 0)

    def test_launch_requires_allowed_loaded_page_and_no_consent_bypass(self):
        self.browser.consent = True
        result = self.executor.execute("app.open", {"app": "youtube"})
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "youtube_consent_required_on_pc")
        self.assertEqual(len(self.browser.calls), 1)  # Navigation only, no consent click.

    def test_pause_command_success_is_insufficient_without_readback(self):
        self.browser.ignored.add(remote._PAUSE)
        result = self.executor.execute("media.pause")
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "pause_or_stop_unverified")
        self.browser.ignored.clear()
        result = self.executor.execute("media.pause")
        self.assertTrue(result["success"])
        self.assertTrue(result["evidence"]["after"]["video"]["paused"])

    def test_play_promise_without_progress_is_blocked(self):
        self.browser.play_progress = False
        result = self.executor.execute("media.play")
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "playback_progress_unverified")

    def test_changed_url_without_changed_content_does_not_verify_track(self):
        self.browser.snapshot["observed_id"] = "9bZkp7q19f0"
        result = self.executor.execute("media.pause")
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "youtube_video_not_ready")

    def test_volume_readback_is_exact_and_mute_is_idempotent(self):
        self.browser.ignored.add(remote._VOLUME)
        result = self.executor.execute("volume.set", {"percent": 65})
        self.assertFalse(result["success"])
        self.browser.ignored.clear()
        result = self.executor.execute("volume.set", {"percent": 65})
        self.assertTrue(result["success"])
        self.assertEqual(result["evidence"]["after"]["video"]["volume"], 0.65)
        self.executor.execute("volume.mute")
        result = self.executor.execute("volume.mute")
        self.assertTrue(result["success"])
        self.assertTrue(result["evidence"]["after"]["video"]["muted"])

    def test_search_never_returns_non_allowlisted_destination(self):
        result = self.executor.execute("youtube.search", {"query": "AC/DC & Rock"})
        self.assertTrue(result["success"])
        self.assertEqual(len(result["evidence"]["results"]), 1)
        self.assertIn("search_query=AC%2FDC+%26+Rock", self.browser.calls[0][1])

    def test_stale_other_query_or_video_does_not_count_as_navigation(self):
        self.browser.snapshot.update(url=remote.YOUTUBE + "results?search_query=Queen")
        self.browser.get = lambda url: None
        result = self.executor.execute("youtube.search", {"query": "Rammstein"})
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "page_load_unverified")
        self.browser.snapshot = state("dQw4w9WgXcQ")
        result = self.executor.execute("url.open", {"url": remote.YOUTUBE + "watch?v=9bZkp7q19f0"})
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "page_load_unverified")

    def test_next_must_change_track_and_previous_uses_observed_history(self):
        result = self.executor.execute("media.prev")
        self.assertFalse(result["success"])
        result = self.executor.execute("media.next")
        self.assertTrue(result["success"])
        self.assertNotEqual(result["evidence"]["before"]["id"], result["evidence"]["after"]["id"])
        result = self.executor.execute("media.prev")
        self.assertTrue(result["success"])
        self.assertEqual(result["evidence"]["after"]["id"], "dQw4w9WgXcQ")

    def test_browser_exception_is_redacted_and_never_verified(self):
        def broken(_script):
            raise RuntimeError("Bearer very-secret https://private.invalid")
        self.browser.execute_script = broken
        result = self.executor.execute("media.pause")
        self.assertFalse(result["success"])
        self.assertFalse(result["evidence"]["verified"])
        self.assertNotIn("secret", str(result))
        self.assertEqual(result["error"], "browser_executor_RuntimeError")

    def test_dead_session_is_replaced_once_before_a_new_open_action(self):
        for failure in ("exception", "empty_windows"):
            with self.subTest(failure=failure):
                stale, fresh = Browser(), Browser()
                factory = mock.Mock(side_effect=[stale, fresh])
                executor = remote.RemoteExecutor(browser_factory=factory, timeout=0)
                self.assertTrue(executor.execute("app.open", {"app": "youtube"})["success"])
                if failure == "exception":
                    stale.alive = False
                else:
                    stale.windows = []
                stale.quit_error = True  # Cleanup of an invalid session may fail too.
                result = executor.execute("app.open", {"app": "youtube"})
                self.assertTrue(result["success"])
                self.assertEqual(factory.call_count, 2)
                self.assertEqual(stale.quit_calls, 1)
                self.assertEqual(fresh.calls, [("navigate", remote.YOUTUBE)])
                self.assertEqual(sum(call[0] == "navigate" for call in stale.calls), 1)

    def test_recovery_rejects_media_context_loss_without_replaying_history(self):
        for intent, args in (("media.prev", {}), ("volume.set", {"percent": 30})):
            with self.subTest(intent=intent):
                stale, fresh = Browser(), Browser()
                fresh.snapshot = state("aqz-KE-bpKQ")
                factory = mock.Mock(side_effect=[stale, fresh])
                executor = remote.RemoteExecutor(browser_factory=factory, timeout=0)
                self.assertTrue(executor.execute("media.next")["success"])
                self.assertEqual(len(executor._history), 2)
                stale.alive = False
                result = executor.execute(intent, args)
                self.assertFalse(result["success"])
                self.assertEqual(result["error"], "browser_context_lost")
                self.assertFalse(result["evidence"]["verified"])
                self.assertEqual(factory.call_count, 2)
                self.assertEqual(stale.quit_calls, 1)
                self.assertEqual(fresh.calls, [])
                self.assertEqual(executor._history, [])

    def test_restored_watch_tab_stays_blocked_until_explicit_verified_navigation(self):
        stale, fresh = Browser(), Browser()
        fresh.snapshot = state("aqz-KE-bpKQ")  # A restored tab is not a newly chosen target.
        factory = mock.Mock(side_effect=[stale, fresh])
        executor = remote.RemoteExecutor(browser_factory=factory, timeout=0)
        self.assertTrue(executor.execute("media.play")["success"])
        stale.alive = False
        for intent, args in (("media.pause", {}), ("media.play", {}), ("volume.set", {"percent": 30})):
            with self.subTest(intent=intent):
                result = executor.execute(intent, args)
                self.assertEqual(result["error"], "browser_context_lost")
                self.assertFalse(result["evidence"]["verified"])
                self.assertEqual(fresh.calls, [])
        self.assertEqual(factory.call_count, 2)

        selected_id = "9bZkp7q19f0"
        selected_url = remote.YOUTUBE + "watch?v=" + selected_id
        self.assertTrue(executor.execute("url.open", {"url": selected_url})["success"])
        result = executor.execute("media.pause")
        self.assertTrue(result["success"])
        self.assertEqual(result["evidence"]["before"]["id"], selected_id)
        self.assertEqual(fresh.calls[0], ("navigate", selected_url))
        self.assertTrue(result["evidence"]["after"]["video"]["paused"])

    def test_failed_or_consent_navigation_cannot_unlock_recovered_media_context(self):
        for failure in ("navigation", "consent"):
            with self.subTest(failure=failure):
                stale, fresh = Browser(), Browser()
                factory = mock.Mock(side_effect=[stale, fresh])
                executor = remote.RemoteExecutor(browser_factory=factory, timeout=0)
                self.assertTrue(executor.execute("app.open", {"app": "youtube"})["success"])
                stale.alive = False
                self.assertEqual(executor.execute("media.pause")["error"], "browser_context_lost")
                navigate = fresh.get
                if failure == "consent":
                    fresh.consent = True
                else:
                    def interrupted_navigation(url):
                        navigate(url)
                        raise RuntimeError("isolated incomplete navigation")
                    fresh.get = interrupted_navigation
                result = executor.execute("app.open", {"app": "youtube"})
                self.assertFalse(result["success"])
                calls_after_navigation = list(fresh.calls)
                self.assertEqual(executor.execute("media.pause")["error"], "browser_context_lost")
                self.assertEqual(fresh.calls, calls_after_navigation)

                fresh.consent = False
                fresh.get = navigate
                self.assertTrue(executor.execute("url.open", {
                    "url": remote.YOUTUBE + "watch?v=9bZkp7q19f0"})["success"])
                self.assertTrue(executor.execute("media.pause")["success"])

    def test_navigation_failure_is_not_retried_until_a_new_action(self):
        stale, fresh = Browser(), Browser()
        factory = mock.Mock(side_effect=[stale, fresh])
        executor = remote.RemoteExecutor(browser_factory=factory, timeout=0)

        def interrupted_navigation(url):
            stale.calls.append(("navigate", url))
            stale.alive = False
            raise RuntimeError("isolated connection loss after navigation")

        stale.get = interrupted_navigation
        result = executor.execute("app.open", {"app": "youtube"})
        self.assertFalse(result["success"])
        self.assertEqual(factory.call_count, 1)
        self.assertEqual(stale.calls, [("navigate", remote.YOUTUBE)])
        self.assertEqual(fresh.calls, [])
        self.assertTrue(executor.execute("app.open", {"app": "youtube"})["success"])
        self.assertEqual(factory.call_count, 2)
        self.assertEqual(fresh.calls, [("navigate", remote.YOUTUBE)])

    def test_next_track_effect_is_never_retried_after_session_loss(self):
        stale, fresh = Browser(), Browser()
        factory = mock.Mock(side_effect=[stale, fresh])
        executor = remote.RemoteExecutor(browser_factory=factory, timeout=0)
        execute_script = stale.execute_script

        def interrupted_next(script, *args):
            result = execute_script(script, *args)
            if script == remote._NEXT:
                stale.alive = False
                raise RuntimeError("isolated session loss after next click")
            return result

        stale.execute_script = interrupted_next
        result = executor.execute("media.next")
        self.assertFalse(result["success"])
        self.assertFalse(result["evidence"]["verified"])
        self.assertEqual(factory.call_count, 1)
        self.assertEqual(sum(call[:2] == ("script", remote._NEXT) for call in stale.calls), 1)
        self.assertEqual(fresh.calls, [])

    def test_volume_effect_is_never_repeated_when_its_readback_loses_session(self):
        stale, fresh = Browser(), Browser()
        factory = mock.Mock(side_effect=[stale, fresh])
        executor = remote.RemoteExecutor(browser_factory=factory, timeout=0)
        execute_script = stale.execute_script

        def interrupted_readback(script, *args):
            if script == remote._SNAPSHOT and not stale.alive:
                raise RuntimeError("isolated session loss before volume readback")
            result = execute_script(script, *args)
            if script == remote._VOLUME:
                stale.alive = False
            return result

        stale.execute_script = interrupted_readback
        result = executor.execute("volume.up")
        self.assertFalse(result["success"])
        self.assertFalse(result["evidence"]["verified"])
        self.assertEqual(factory.call_count, 1)
        self.assertEqual(sum(call[:2] == ("script", remote._VOLUME) for call in stale.calls), 1)
        self.assertAlmostEqual(stale.snapshot["video"]["volume"], 0.5)
        self.assertEqual(fresh.calls, [])

    def test_failed_replacement_does_not_loop_or_reuse_stale_driver(self):
        stale = Browser()
        factory = mock.Mock(side_effect=[stale, remote.ActionError("isolated_browser_start_failed")])
        executor = remote.RemoteExecutor(browser_factory=factory, timeout=0)
        self.assertTrue(executor.execute("app.open", {"app": "youtube"})["success"])
        stale.alive = False
        result = executor.execute("app.open", {"app": "youtube"})
        self.assertFalse(result["success"])
        self.assertEqual(factory.call_count, 2)
        self.assertEqual(stale.quit_calls, 1)
        self.assertIsNone(executor._driver)


if __name__ == "__main__":
    unittest.main()
