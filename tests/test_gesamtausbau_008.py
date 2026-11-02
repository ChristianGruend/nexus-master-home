"""008: Regressionstests fuer Sprechererkennung, Weckwort-Varianten, User-Skills."""
import io, wave
import numpy as np
import pytest
from nexus.voice import commands, speaker_id
from nexus.skills import registry


def _wav(freqs, sec=1.5, sr=16000):
    t = np.arange(int(sec * sr)) / sr
    x = sum(np.sin(2 * np.pi * f * t) / (i + 1) for i, f in enumerate(freqs)) * (0.5 + 0.5 * np.sin(2 * np.pi * 3 * t))
    b = io.BytesIO()
    with wave.open(b, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes((x / np.abs(x).max() * 20000).astype("<i2").tobytes())
    return b.getvalue()


def test_speaker_enroll_identify(tmp_path, monkeypatch):
    monkeypatch.setattr(speaker_id, "STORE", tmp_path / "s.json")
    assert speaker_id.identify(_wav([200, 400]))["error"] == "NO_ENROLLED_SPEAKER"
    speaker_id.enroll("a", [_wav([200, 400, 800])])
    assert speaker_id.identify(_wav([200, 400, 800], sec=2.0))["speaker"] == "a"
    with pytest.raises(ValueError):
        speaker_id.embed(_wav([200], sec=0.2))


@pytest.mark.parametrize("t", ["Herr Innexus, öffne den Rechner.", "Hey NEXUS, öffne den Rechner."])
def test_wake_variants(t):
    assert commands.parse(t)["intent"] == "open_pc_app"


def test_wake_no_false_positive():
    assert commands.parse("Herr Meier kommt morgen.")["intent"] == "chat"


def test_user_skill_guards(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "USER_SKILLS", tmp_path / "u.json")
    assert registry.create_user_skill("x", [{"action": "read_file"}])["error"] == "invalid_skill_name"
    assert registry.create_user_skill("priv_test", [{"action": "nexus_restart"}])["error"] == "privileged_not_allowed_in_skill"
    assert registry.create_user_skill("ok_skill", [{"action": "read_file", "params": {"path": "{p}"}}])["success"]
    assert registry.create_user_skill("ok_skill", [{"action": "read_file"}])["error"] == "skill_exists"
    assert registry._fill({"a": ["{p}"]}, {"p": "z"}) == {"a": ["z"]}
    assert any(s["name"] == "ok_skill" for s in registry.list_user_skills())
