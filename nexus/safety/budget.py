"""Phase 1 (WP3): deterministischer BudgetGuard fuer Aether-Laeufe.

Prueft harte Obergrenzen VOR jedem Modellaufruf, Werkzeugaufruf und Spawn.
Kein LLM entscheidet ueber das Budget; Ueberschreitung bricht den Lauf ab.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field


class BudgetExceeded(RuntimeError):
    def __init__(self, kind: str, limit, used) -> None:
        self.kind, self.limit, self.used = str(kind), limit, used
        super().__init__(f"BUDGET_EXCEEDED:{self.kind} ({used}/{limit})")


@dataclass
class Budget:
    wall_clock_s: float = 600.0
    model_calls: int = 24
    tool_calls: int = 40
    max_agents: int = 4
    max_tokens: int = 20000
    max_vram_mb: int = 5600
    started: float = field(default_factory=time.time)
    model_calls_used: int = 0
    tool_calls_used: int = 0
    agents_used: int = 0
    tokens_used: int = 0

    def elapsed(self) -> float:
        return max(0.0, time.time() - self.started)

    def remaining_s(self) -> float:
        return max(0.0, float(self.wall_clock_s) - self.elapsed())

    def check_wall(self) -> None:
        if self.elapsed() > float(self.wall_clock_s):
            raise BudgetExceeded("wall_clock_s", self.wall_clock_s, round(self.elapsed(), 1))

    def consume_model(self, tokens: int = 0) -> None:
        self.check_wall()
        self.model_calls_used += 1
        if int(tokens or 0) > 0:
            self.tokens_used += int(tokens)
        if self.model_calls_used > int(self.model_calls):
            raise BudgetExceeded("model_calls", self.model_calls, self.model_calls_used)
        if self.tokens_used > int(self.max_tokens):
            raise BudgetExceeded("max_tokens", self.max_tokens, self.tokens_used)

    def add_tokens(self, tokens: int) -> None:
        self.tokens_used += max(0, int(tokens or 0))
        if self.tokens_used > int(self.max_tokens):
            raise BudgetExceeded("max_tokens", self.max_tokens, self.tokens_used)

    def consume_tool(self) -> None:
        self.check_wall()
        self.tool_calls_used += 1
        if self.tool_calls_used > int(self.tool_calls):
            raise BudgetExceeded("tool_calls", self.tool_calls, self.tool_calls_used)

    def consume_agent(self) -> None:
        self.check_wall()
        self.agents_used += 1
        if self.agents_used > int(self.max_agents):
            raise BudgetExceeded("max_agents", self.max_agents, self.agents_used)

    def snapshot(self) -> dict:
        return {
            "wall_clock_s": self.wall_clock_s,
            "elapsed_s": round(self.elapsed(), 1),
            "model_calls": self.model_calls,
            "model_calls_used": self.model_calls_used,
            "tool_calls": self.tool_calls,
            "tool_calls_used": self.tool_calls_used,
            "max_agents": self.max_agents,
            "agents_used": self.agents_used,
            "max_tokens": self.max_tokens,
            "tokens_used": self.tokens_used,
            "max_vram_mb": self.max_vram_mb,
        }
