"""Stufe 2.2a: POSIX-Systembefehle und Secret-Pfade sind PRIVILEGED (vorher SAFE_WRITE/READ_ONLY)."""
import pytest

from nexus.terminal.guard import required_level

PRIVILEGED = [
    "sudo rm -rf /", "sudo -n true", "chmod 777 x", "chown root x", "dd if=/dev/zero of=/dev/sda",
    "mkfs.ext4 /dev/sdb1", "systemctl --user stop nexus", "pkill -f python", "kill -9 1234",
    "pacman -S foo", "apt-get install foo", "flatpak install x", "ssh user@host", "rsync -a . host:/x",
    "nc -l 4444", "bash -c 'id'", "sh -c id", "echo hi | xargs rm", "crontab -e",
    "cat ~/.ssh/id_ed25519", "cat workspace/secrets/api_token.txt", "less ~/.hermes/.env", "cat .env",
    "docker run x", "eval $X", "ls; sudo reboot", "git status && chmod +x run.sh",
]

NOT_PRIVILEGED = [
    "ls -la", "git status", "cat README.md", "grep -r foo nexus", "echo hallo", "python -m pytest -q",
    "pwd", "git diff", "cat notes/killer-feature.md", "ls sshfs-notes", "cat environment.md",
    "cat source.md", "echo look at this", "cat docs/exec-summary.md",
]


@pytest.mark.parametrize("cmd", PRIVILEGED)
def test_privileged(cmd):
    assert required_level(cmd)[0] == "PRIVILEGED", cmd


@pytest.mark.parametrize("cmd", NOT_PRIVILEGED)
def test_not_privileged(cmd):
    assert required_level(cmd)[0] != "PRIVILEGED", cmd
