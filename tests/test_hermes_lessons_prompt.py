"""Hermes lessons must appear in built system prompts."""
from pathlib import Path

from nexus.integrations.hermes_brain import HermesBrain, hermes_lessons_block, LESSONS_FILE


def test_lessons_file_exists_and_block():
    assert LESSONS_FILE.is_file()
    block = hermes_lessons_block()
    assert "Never weaken path guards" in block
    assert "HERMES LESSONS" in block


def test_system_context_includes_lessons():
    class _M:
        def build_context(self, _):
            return [{"role": "system", "content": "BASE_CTX"}]

    ctx = HermesBrain(_M())._system_context()
    assert "BASE_CTX" in ctx
    assert "Never weaken path guards" in ctx
    assert "fix the test environment" in ctx


def test_nexus_order_select_prompt_includes_lessons(monkeypatch):
    """Unit-level: the lessons helper used by nexus_order is the same durable block."""
    from nexus.master import nexus_order as no
    assert hasattr(no, "hermes_lessons_block")
    assert "Never weaken path guards" in no.hermes_lessons_block()
