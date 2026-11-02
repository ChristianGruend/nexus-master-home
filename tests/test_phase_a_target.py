"""An explicit Phase-A target never falls back to another connected phone."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from nexus import nexus_os
from nexus.bridge.errors import BridgeError
from nexus.bridge.executors import _phase_a_forward, _phase_a_health


class PhaseATargetTests(unittest.TestCase):
    def _devices(self, target_state=None):
        devices = [{"serial": "other-phone", "state": "device"}]
        if target_state is not None:
            devices.append({"serial": "249ac5d7", "state": target_state})
        return {"adb": True, "devices": devices,
                "ready": [d for d in devices if d["state"] == "device"], "error": None}

    def test_missing_target_never_forwards_or_reads_another_phone(self):
        with patch.object(nexus_os, "adb_devices", return_value=self._devices()), \
             patch.object(nexus_os, "ensure_adb_forward") as forward, \
             patch("urllib.request.urlopen") as request:
            with self.assertRaises(BridgeError) as raised:
                _phase_a_health({"device": "249ac5d7"})
        self.assertEqual(raised.exception.details["code"], "SERIAL_NOT_FOUND")
        forward.assert_not_called()
        request.assert_not_called()

    def test_unauthorized_and_offline_target_never_forward(self):
        for state, code in (("unauthorized", "DEVICE_UNAUTHORIZED"), ("offline", "DEVICE_OFFLINE")):
            with self.subTest(state=state), \
                 patch.object(nexus_os, "adb_devices", return_value=self._devices(state)), \
                 patch.object(nexus_os, "ensure_adb_forward") as forward:
                with self.assertRaises(BridgeError) as raised:
                    _phase_a_forward({"device": "249ac5d7"})
                self.assertEqual(raised.exception.details["code"], code)
                forward.assert_not_called()

    def test_exact_device_uses_checked_snapshot_without_legacy_cleanup(self):
        devices = self._devices("device")
        with patch.object(nexus_os, "adb_devices", return_value=devices) as listing, \
             patch.object(nexus_os, "ensure_adb_forward",
                          return_value={"ok": True, "serial": "249ac5d7"}) as forward:
            port = _phase_a_forward({"device": "249ac5d7"})
        self.assertEqual(port, nexus_os.PHASE_A_FORWARD_PORT)
        listing.assert_called_once_with()
        forward.assert_called_once_with(nexus_os.PHASE_A_FORWARD_PORT, nexus_os.PHASE_A_REMOTE_PORT,
                                        "249ac5d7", devices=devices, cleanup_legacy=False)

    def test_unexpected_forward_target_never_reaches_http(self):
        with patch.object(nexus_os, "adb_devices", return_value=self._devices("device")), \
             patch.object(nexus_os, "ensure_adb_forward",
                          return_value={"ok": True, "serial": "other-phone"}), \
             patch("urllib.request.urlopen") as request:
            with self.assertRaises(BridgeError) as raised:
                _phase_a_health({"device": "249ac5d7"})
        self.assertEqual(raised.exception.details["code"], "ADB_TARGET_MISMATCH")
        request.assert_not_called()

    def test_missing_adb_is_exact_error_without_forwarding(self):
        with patch.object(nexus_os, "adb_devices", return_value={"adb": False, "devices": []}), \
             patch.object(nexus_os, "ensure_adb_forward") as forward:
            with self.assertRaises(BridgeError) as raised:
                _phase_a_forward({"device": "249ac5d7"})
        self.assertEqual(raised.exception.details["code"], "ADB_NOT_FOUND")
        forward.assert_not_called()

    def test_unspecified_device_keeps_existing_registered_behavior(self):
        with patch.object(nexus_os, "adb_devices") as listing, \
             patch.object(nexus_os, "ensure_adb_forward", return_value={"ok": True}) as forward:
            self.assertEqual(_phase_a_forward({}), nexus_os.PHASE_A_FORWARD_PORT)
        listing.assert_not_called()
        forward.assert_called_once_with(nexus_os.PHASE_A_FORWARD_PORT, nexus_os.PHASE_A_REMOTE_PORT, "")


if __name__ == "__main__":
    unittest.main()
