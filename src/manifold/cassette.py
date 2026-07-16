"""Recorded tool cassettes — mocks that are *recordings of a real tool*, not inventions.

The honest gap this closes: elsewhere in the examples, a tool's response pool is authored
by hand (``responses=["search-result-A", ...]``) — strings a human made up. A pass against
those mocks means "correct against responses I imagined." Nothing measured whether they
resembled a real tool.

A **cassette** flips that. You run the *real* tool once, capture exactly what it returned,
and the resulting ``Take`` list becomes the ``ToolSpec.responses`` pool. The mock is then a
recording, not an invention — and, crucially, its fidelity is **checkable**: ``drift()``
re-runs every recorded call against the live tool and reports where reality has moved out
from under the recording. That check is the part that was missing.

Scope, honestly (see the module docstring of ``examples/real_tools.py``): this makes the
*response* side real and drift-checked. It does not make **faults** real — those stay
deliberate *models* of failure modes (error/timeout/garbage/latency), not emulation of an
external system (a DESIGN scope decision). And a recording is a *sample* of a real tool's
behavior space, never the whole of it.

Clock-free by construction: ``record`` takes ``recorded_at`` from its caller rather than
reading the clock, so nothing here can leak wall-clock into scenario construction (ADR-0002).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Take:
    """One recorded real call: the arguments, and what the real tool actually returned."""

    args: dict[str, Any]
    response: Any


@dataclass
class Cassette:
    """Recorded real-tool behavior: ``tool name -> the takes captured for it``."""

    tools: dict[str, list[Take]] = field(default_factory=dict)
    recorded_at: str = ""  # supplied by the recorder; never read from the clock here
    source_digest: str = ""  # what it was recorded against (e.g. a corpus hash)

    def responses(self, tool: str) -> list[Any]:
        """The real responses recorded for ``tool`` — a ready-made ToolSpec pool."""
        return [t.response for t in self.tools.get(tool, [])]

    def dump(self, path: str | Path) -> None:
        payload = {
            "recorded_at": self.recorded_at,
            "source_digest": self.source_digest,
            "tools": {
                name: [{"args": t.args, "response": t.response} for t in takes]
                for name, takes in self.tools.items()
            },
        }
        Path(path).write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> Cassette:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        tools = {
            name: [Take(args=t["args"], response=t["response"]) for t in takes]
            for name, takes in raw.get("tools", {}).items()
        }
        return cls(
            tools=tools,
            recorded_at=raw.get("recorded_at", ""),
            source_digest=raw.get("source_digest", ""),
        )


def digest(*texts: str) -> str:
    """A stable 12-hex digest of what a cassette was recorded against."""
    h = hashlib.sha256()
    for t in texts:
        h.update(t.encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()[:12]


def record(
    tools: dict[str, Callable[..., Any]],
    calls: dict[str, list[dict[str, Any]]],
    *,
    recorded_at: str = "",
    source_digest: str = "",
) -> Cassette:
    """Run each **real** tool over its argument list and capture what it actually returns.

    ``tools`` maps a name to the real callable; ``calls`` maps the same names to the arg
    dicts to record. Exceptions are captured as their string form — a real tool's error is
    part of its real behavior.
    """
    out: dict[str, list[Take]] = {}
    for name, fn in tools.items():
        takes: list[Take] = []
        for args in calls.get(name, []):
            try:
                response: Any = fn(**args)
            except Exception as exc:  # a real error IS real behavior — record it
                response = f"{type(exc).__name__}: {exc}"
            takes.append(Take(args=dict(args), response=response))
        out[name] = takes
    return Cassette(tools=out, recorded_at=recorded_at, source_digest=source_digest)


def drift(cassette: Cassette, tools: dict[str, Callable[..., Any]]) -> list[str]:
    """Re-run every recorded call against the **live** tools; report where they disagree.

    An empty list means the recording still matches reality — the mocks are faithful. A
    non-empty list means the cassette is stale: the tool or its data moved, and any pass
    measured against it is measuring the past. This is the check that makes "are the mocks
    faithful?" an answerable question instead of a hand-wave.
    """
    drifts: list[str] = []
    for name, takes in cassette.tools.items():
        fn = tools.get(name)
        if fn is None:
            drifts.append(f"{name}: recorded, but no live tool supplied to check against")
            continue
        for take in takes:
            try:
                live: Any = fn(**take.args)
            except Exception as exc:
                live = f"{type(exc).__name__}: {exc}"
            if live != take.response:
                drifts.append(
                    f"{name}({take.args}): recorded {take.response!r:.60} "
                    f"but live returns {live!r:.60}"
                )
    return drifts
