"""Phase 2/7c: pc_music faellt auf den lokalen Player zurueck (offline, Fakes)."""
from __future__ import annotations

import os
import unittest
from unittest import mock

from nexus.computer import remote_executor as remote

OK = {"success": True, "state": {"index": 0, "playing": True, "count": 2,
                                 "current": {"name": "a.mp3", "path": "Musik/a.mp3"}}}


def yt(error=None):
    if error:
        return {"success": False, "error": error, "evidence": {"verified": False, "backend": "controlled_pc_browser"}}
    return {"success": True, "error": None, "evidence": {"verified": True, "backend": "controlled_pc_browser"}}


class Media:
    def __init__(self, result):
        self.result, self.calls = result, []

    def play_at(self, i):
        self.calls.append(i)
        return self.result


def run(yt_result, args, media_result=OK, env="1"):
    ex = mock.Mock()
    ex.execute.return_value = yt_result
    media = Media(media_result)
    with mock.patch.dict(os.environ, {"NEXUS_PC_MUSIC_LOCAL_FALLBACK": env}), \
            mock.patch.object(remote, "_EXECUTOR", ex), \
            mock.patch.object(remote, "_in_process_media", return_value=media), \
            mock.patch.object(remote, "_http_play_at", side_effect=AssertionError("http not expected")):
        return remote.execute("media.play_search", args), ex, media


Q = {"query": "lofi", "target": "youtube"}


class LocalFallback(unittest.TestCase):
    def test_ad_falls_back(self):
        r, ex, m = run(yt("youtube_ad_active"), Q)
        self.assertTrue(r["success"])
        self.assertEqual(r["evidence"]["backend"], "local_player")
        self.assertEqual(r["evidence"]["fallback_reason"], "youtube_ad_active")
        self.assertIs(r["evidence"]["verified"], True)
        self.assertEqual(r["evidence"]["path"], "Musik/a.mp3")
        self.assertEqual(m.calls, [0])

    def test_consent_falls_back(self):
        r, ex, m = run(yt("youtube_consent_required_on_pc"), Q)
        self.assertEqual(r["evidence"]["fallback_reason"], "youtube_consent_required_on_pc")
        self.assertIs(r["evidence"]["verified"], True)

    def test_no_query_falls_back_without_youtube(self):
        r, ex, m = run(yt(), {"target": "youtube"})
        self.assertEqual(r["evidence"]["fallback_reason"], "no_youtube_query")
        ex.execute.assert_not_called()

    def test_youtube_success_no_fallback(self):
        r, ex, m = run(yt(), Q)
        self.assertEqual(r["evidence"]["backend"], "controlled_pc_browser")
        self.assertEqual(m.calls, [])

    def test_play_at_fails_not_verified(self):
        r, ex, m = run(yt("youtube_ad_active"), Q, media_result={"success": False, "error": "empty", "state": {"current": None}})
        self.assertFalse(r["success"])
        self.assertEqual(r["error"], "empty")
        self.assertIs(r["evidence"]["verified"], False)
        self.assertEqual(r["evidence"]["backend"], "local_player")

    def test_play_at_success_without_path_not_verified(self):
        r, ex, m = run(yt("youtube_ad_active"), Q, media_result={"success": True, "state": {"current": {"name": "x"}}})
        self.assertIs(r["evidence"]["verified"], False)

    def test_other_youtube_error_no_fallback(self):
        r, ex, m = run(yt("youtube_player_muted"), Q)
        self.assertEqual(r["error"], "youtube_player_muted")
        self.assertEqual(m.calls, [])

    def test_switch_off_old_behaviour(self):
        r, ex, m = run(yt("youtube_ad_active"), Q, env="0")
        self.assertEqual(r["error"], "youtube_ad_active")
        self.assertEqual(m.calls, [])

    def test_http_used_when_no_engine(self):
        ex = mock.Mock()
        ex.execute.return_value = yt("youtube_ad_active")
        with mock.patch.dict(os.environ, {"NEXUS_PC_MUSIC_LOCAL_FALLBACK": "1"}), \
                mock.patch.object(remote, "_EXECUTOR", ex), \
                mock.patch.object(remote, "_in_process_media", return_value=None), \
                mock.patch.object(remote, "_http_play_at", return_value=OK) as h:
            r = remote.execute("media.play_search", Q)
        h.assert_called_once_with(0)
        self.assertEqual(r["evidence"]["via"], "http_loopback")
        self.assertIs(r["evidence"]["verified"], True)


if __name__ == "__main__":
    unittest.main()
