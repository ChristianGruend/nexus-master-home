import os


from nexus.common.proc_env import is_secret_name, scrub


def test_is_secret_name_true():
    assert is_secret_name("NEXUS_BRIDGE_TOKEN")
    assert is_secret_name("GITHUB_TOKEN")
    assert is_secret_name("OPENAI_API_KEY")
    assert is_secret_name("AWS_SECRET_ACCESS_KEY")
    assert is_secret_name("NEXUS_MAIL_APP_PASSWORD")
    assert is_secret_name("SSH_AUTH_SOCK")
    assert is_secret_name("NEXUS_AGENT_TOKEN")
    assert is_secret_name("hf_token")


def test_is_secret_name_false():
    assert not is_secret_name("PATH")
    assert not is_secret_name("HOME")
    assert not is_secret_name("LANG")
    assert not is_secret_name("PYTHONIOENCODING")
    assert not is_secret_name("NEXUS_HOST")


def test_scrub_removes_secrets():
    assert scrub({"PATH": "/bin", "GITHUB_TOKEN": "x"}) == {"PATH": "/bin"}


def test_scrub_keep():
    assert scrub({"GITHUB_TOKEN": "x"}, keep=["github_token"]) == {"GITHUB_TOKEN": "x"}


def test_scrub_extra():
    assert scrub({"PATH": "/bin"}, FOO="1")["FOO"] == "1"


def test_scrub_default_env(monkeypatch):
    monkeypatch.setenv("NEXUS_BRIDGE_TOKEN", "abc")
    assert "NEXUS_BRIDGE_TOKEN" not in scrub()


def test_ensure_bridge_token_no_env(monkeypatch, tmp_path):
    from nexus.bridge import auth

    monkeypatch.delenv("NEXUS_BRIDGE_TOKEN", raising=False)
    monkeypatch.setattr(auth, "TOKEN_REL_CANDIDATES", (tmp_path / "t",))
    monkeypatch.setattr(auth, "token_path", lambda: tmp_path / "t")
    auth.ensure_bridge_token()
    assert "NEXUS_BRIDGE_TOKEN" not in os.environ


def test_display_auth_vars_are_kept():
    assert not is_secret_name("XAUTHORITY")
    assert not is_secret_name("WAYLAND_DISPLAY")
