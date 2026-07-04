"""The coverage model as data (DESIGN.md §4.2, §6.3).

A coverage model is built from four primitives — value-level **coverpoints** (with
**bins**), edge-level **transition** coverpoints over an FSM on the trace, and
**crosses** of two coverpoints. They hold callables (extractors / predicates) and so
are code-as-config: dataclasses, never serialised. (The ``CoverageDB`` they evaluate
into *is* pure data — see ``coverage.py``.)

This module also ships a small **starter library** of generic, tool-name-agnostic
coverpoints so a useful default model exists with zero authoring (``default_model``),
plus ``tool_called_cp`` for specs that know their tool set.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from manifold.scenario import Scenario
from manifold.trace import Terminal, ToolResult, Trace

# A projection predicts, from a scenario's *inputs alone* (no agent run), which bins it
# can hit. It's what lets coverage-directed generation target holes across every
# input-derivable coverpoint — not just `fault_seen` (ADR-0004, H1). Optional per
# coverpoint: behaviour-derived coverpoints (e.g. terminal_reason) simply omit it.
Projection = Callable[[Scenario], "set[str]"]


@dataclass(frozen=True)
class Bin:
    name: str
    predicate: Callable[[Any], bool]  # value-level: a bin is hit if any extracted value matches


@dataclass(frozen=True)
class Coverpoint:
    name: str
    extract: Callable[[Trace], Iterable[Any]]  # pull the values of interest from a trace
    bins: list[Bin]
    project: Projection | None = None  # input-derivable bins, for coverage-directed selection
    ignore: frozenset[str] = frozenset()  # declared bins that are structurally unreachable (H4)


@dataclass(frozen=True)
class TransitionCoverpoint:
    name: str
    symbols: Callable[[Trace], list[str]]  # trace -> FSM symbol sequence
    edges: list[tuple[str, str]]  # the edges to cover (these are the "bins")
    project: Projection | None = None
    ignore: frozenset[str] = frozenset()  # edge names ("a->b") that can't occur


@dataclass(frozen=True)
class Cross:
    name: str
    a: str  # coverpoint name
    b: str  # coverpoint name
    project: Projection | None = None
    ignore: frozenset[str] = frozenset()  # cell names ("x x y") that are illegal/impossible


@dataclass
class CoverageModel:
    coverpoints: list[Coverpoint] = field(default_factory=list)
    transitions: list[TransitionCoverpoint] = field(default_factory=list)
    crosses: list[Cross] = field(default_factory=list)

    def coverpoint(self, name: str) -> Coverpoint | None:
        return next((c for c in self.coverpoints if c.name == name), None)


# --- bin-predicate helpers -------------------------------------------------

def _eq(target: Any) -> Callable[[Any], bool]:
    return lambda v: bool(v == target)


def _between(lo: int, hi: int) -> Callable[[Any], bool]:
    return lambda v: isinstance(v, int) and lo <= v <= hi


def _ge(lo: int) -> Callable[[Any], bool]:
    return lambda v: isinstance(v, int) and v >= lo


# --- starter library of generic coverpoints --------------------------------

def n_tool_calls_cp() -> Coverpoint:
    """How many tool calls the agent made — a coarse shape-of-run signal."""
    return Coverpoint(
        "n_tool_calls",
        lambda t: [len(t.tools_called())],
        [
            Bin("0", _eq(0)),
            Bin("1", _eq(1)),
            Bin("2-3", _between(2, 3)),
            Bin("4-8", _between(4, 8)),
            Bin("9+", _ge(9)),
        ],
    )


_REASONS = ["completed", "budget_steps", "budget_cost", "timeout", "error"]


def terminal_reason_cp() -> Coverpoint:
    """Which way the run ended."""
    return Coverpoint(
        "terminal_reason",
        lambda t: [t.terminal.reason] if isinstance(t.terminal, Terminal) else [],
        [Bin(r, _eq(r)) for r in _REASONS],
    )


_FAULTS = ["none", "error", "timeout", "garbage", "latency"]


def _faults_seen(t: Trace) -> list[str]:
    seen = [e.fault for e in t.events if isinstance(e, ToolResult) and e.fault]
    return seen or ["none"]


def _fault_projection(scn: Scenario) -> set[str]:
    kinds = {str(f.kind) for m in scn.mocks for f in m.faults}
    return kinds or {"none"}


def fault_seen_cp() -> Coverpoint:
    """Which injected fault kinds the run actually exercised."""
    return Coverpoint(
        "fault_seen", _faults_seen, [Bin(f, _eq(f)) for f in _FAULTS], project=_fault_projection
    )


def tool_called_cp(tools: list[str]) -> Coverpoint:
    """Per-tool coverage — needs the declared tool set, so it lives in a spec."""
    tset = set(tools)

    def project(scn: Scenario) -> set[str]:
        return {m.tool for m in scn.mocks if m.tool in tset}

    return Coverpoint(
        "tool_called", lambda t: t.tools_called(), [Bin(x, _eq(x)) for x in tools], project=project
    )


def fault_by_tool_cp(tools: list[str], kinds: list[str] | None = None) -> Coverpoint:
    """Which (tool, fault-kind) combinations were exercised — the richest input-derivable
    coverpoint and the one coverage-directed generation can target exactly. |tools|x|kinds|
    bins, every one reachable (the generator can inject any kind on any tool)."""
    ks = kinds or ["error", "timeout", "garbage", "latency"]

    def extract(t: Trace) -> list[str]:
        return [f"{e.tool}:{e.fault}" for e in t.events if isinstance(e, ToolResult) and e.fault]

    def project(scn: Scenario) -> set[str]:
        return {f"{m.tool}:{f.kind}" for m in scn.mocks for f in m.faults}

    bins = [Bin(f"{tool}:{k}", _eq(f"{tool}:{k}")) for tool in tools for k in ks]
    return Coverpoint("fault_by_tool", extract, bins, project=project)


def default_model() -> CoverageModel:
    """A useful zero-config model: generic, tool-name-agnostic coverpoints. Specs add
    tool-specific coverpoints, an FSM, and crosses on top (see examples/spec_example.py)."""
    return CoverageModel(coverpoints=[n_tool_calls_cp(), terminal_reason_cp(), fault_seen_cp()])
