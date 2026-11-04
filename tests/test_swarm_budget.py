import time

import pytest

from nexus.safety.budget import Budget, BudgetExceeded


def test_model_call_limit():
    b = Budget(model_calls=2)
    b.consume_model(); b.consume_model()
    with pytest.raises(BudgetExceeded, match="model_calls"):
        b.consume_model()


def test_tool_call_limit():
    b = Budget(tool_calls=1)
    b.consume_tool()
    with pytest.raises(BudgetExceeded, match="tool_calls"):
        b.consume_tool()


def test_agent_limit():
    b = Budget(max_agents=1)
    b.consume_agent()
    with pytest.raises(BudgetExceeded, match="max_agents"):
        b.consume_agent()


def test_token_limit():
    b = Budget(max_tokens=10)
    b.add_tokens(5)
    with pytest.raises(BudgetExceeded, match="max_tokens"):
        b.add_tokens(6)


def test_wall_clock():
    b = Budget(wall_clock_s=0.01)
    time.sleep(0.03)
    with pytest.raises(BudgetExceeded, match="wall_clock_s"):
        b.check_wall()


def test_snapshot_shape():
    b = Budget(model_calls=3)
    b.consume_model()
    s = b.snapshot()
    assert s["model_calls"] == 3 and s["model_calls_used"] == 1
    assert s["max_vram_mb"] == 5600
