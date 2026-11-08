"""Direkte Vertragstests für die Sicherheitsfilter vor Tool-Ausführung."""
from nexus.master.tool_scope import filter_tool_calls


def _filter(calls, message, completed=(), required=(), technical_diag=False):
    return filter_tool_calls(calls, message, technical_diag, set(), set(required), lambda: set(completed))


def test_read_only_request_rejects_unasked_write_and_shell():
    calls = [
        {"name": "status", "args": {}},
        {"name": "write_file", "args": {"path": "workspace/files/a.txt", "content": "X"}},
        {"name": "powershell", "args": {"command": "Remove-Item a.txt"}},
        {"name": "read_file", "args": {"path": "workspace/files/a.txt"}},
    ]
    assert [c["name"] for c in _filter(calls, "Lies workspace/files/a.txt")] == ["read_file"]


def test_required_order_and_single_mutation_per_round():
    calls = [
        {"name": "write_file", "args": {"path": "workspace/files/a.txt", "content": "X"}},
        {"name": "write_file", "args": {"path": "workspace/files/b.txt", "content": "Y"}},
        {"name": "read_file", "args": {"path": "workspace/files/a.txt"}},
        {"name": "powershell", "args": {"command": "Write-Output OK"}},
    ]
    message = "Schreibe und lies eine Datei, führe danach PowerShell aus"
    assert [c["name"] for c in _filter(calls, message, required=("write", "read", "exec"))] == ["write_file"]
    assert [c["name"] for c in _filter(calls[2:], message, completed=("write",),
                                         required=("write", "read", "exec"))] == ["read_file"]
    assert [c["name"] for c in _filter(calls[3:], message, completed=("write", "read"),
                                         required=("write", "read", "exec"))] == ["powershell"]


def test_repeated_call_and_unasked_prompt_mutation_are_dropped():
    repeated = {"name": "read_file", "args": {"path": "workspace/files/a.txt"}}
    master_prompt = {"name": "write_file", "args": {"path": "workspace/master_prompt_runtime.txt", "content": "X"}}
    key = ("read_file", '{"path": "workspace/files/a.txt"}')
    assert filter_tool_calls([repeated, master_prompt], "Schreibe einen Bericht", False,
                             {key}, set(), lambda: set()) == []
