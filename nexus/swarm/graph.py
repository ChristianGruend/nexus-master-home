"""Schlanke Graph-Engine (LangGraph-artig, ohne pip): Nodes, bedingte Edges, State, Timeout pro Schritt."""
from __future__ import annotations
import threading, time
from typing import Callable, Optional

END = "__end__"


class StepTimeout(Exception):
    pass


def run_with_timeout(fn: Callable, state: dict, timeout: float):
    box: dict = {}
    def _t():
        try:
            box["r"] = fn(state)
        except BaseException as e:  # noqa
            box["e"] = e
    th = threading.Thread(target=_t, daemon=True)
    th.start()
    th.join(timeout)
    if th.is_alive():
        raise StepTimeout(f"timeout nach {timeout:.0f}s")
    if "e" in box:
        raise box["e"]
    return box.get("r")


class Graph:
    def __init__(self):
        self.nodes: dict[str, Callable] = {}
        self.timeouts: dict[str, float] = {}
        self.edges: dict[str, list] = {}
        self.entry: Optional[str] = None

    def add_node(self, name: str, fn: Callable, timeout: float = 60):
        self.nodes[name] = fn
        self.timeouts[name] = timeout
        if self.entry is None:
            self.entry = name
        return self

    def add_edge(self, src: str, dst: str, cond: Optional[Callable] = None):
        self.edges.setdefault(src, []).append((dst, cond))
        return self

    def next(self, node: str, state: dict) -> str:
        for dst, cond in self.edges.get(node, []):
            if cond is None or cond(state):
                return dst
        return END

    def run(self, state: dict, on_step: Optional[Callable] = None, max_steps: int = 30) -> dict:
        state.setdefault("trace", [])
        node = self.entry
        steps = 0
        while node and node != END and steps < max_steps:
            steps += 1
            state["active"] = node
            t0 = time.time()
            rec = {"node": node, "start": t0, "status": "running"}
            state["trace"].append(rec)
            if on_step:
                on_step(state)
            try:
                out = run_with_timeout(self.nodes[node], state, self.timeouts[node])
                if isinstance(out, dict):
                    state.update(out)
                rec["status"] = "ok"
            except StepTimeout as e:
                rec["status"] = "timeout"; rec["error"] = str(e)
                state.setdefault("timeouts", []).append(node)
            except PermissionError as e:
                rec["status"] = "blocked"; rec["error"] = str(e)
                state["blocked"] = str(e)
            except Exception as e:
                rec["status"] = "error"; rec["error"] = f"{type(e).__name__}: {e}"
                state.setdefault("errors", []).append(f"{node}: {e}")
            rec["sec"] = round(time.time() - t0, 2)
            if on_step:
                on_step(state)
            node = self.next(node, state)
        state["active"] = None
        return state
