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

from manifold.trace import Terminal, ToolResult, Trace


@dataclass(frozen=True)
class Bin:
    name: str
    predicate: Callable[[Any], bool]  # value-level: a bin is hit if any extracted value matches


@dataclass(frozen=True)
class Coverpoint:
    name: str
    extract: Callable[[Trace], Iterable[Any]]  # pull the values of interest from a trace
    bins: list[Bin]


@dataclass(frozen=True)
class TransitionCoverpoint:
    name: str
    symbols: Callable[[Trace], list[str]]  # trace -> FSM symbol sequence
    edges: list[tuple[str, str]]  # the edges to cover (these are the "bins")


@dataclass(frozen=True)
class Cross:
    name: str
    a: str  # coverpoint name
    b: str  # coverpoint name


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


def fault_seen_cp() -> Coverpoint:
    """Which injected fault kinds the run actually exercised."""
    return Coverpoint("fault_seen", _faults_seen, [Bin(f, _eq(f)) for f in _FAULTS])


def tool_called_cp(tools: list[str]) -> Coverpoint:
    """Per-tool coverage — needs the declared tool set, so it lives in a spec."""
    return Coverpoint("tool_called", lambda t: t.tools_called(), [Bin(x, _eq(x)) for x in tools])


def default_model() -> CoverageModel:
    """A useful zero-config model: generic, tool-name-agnostic coverpoints. Specs add
    tool-specific coverpoints, an FSM, and crosses on top (see examples/spec_example.py)."""
    return CoverageModel(coverpoints=[n_tool_calls_cp(), terminal_reason_cp(), fault_seen_cp()])
