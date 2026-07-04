"""Coverage evaluation (DESIGN.md §4.2, §6.3).

``evaluate(trace, model)`` is a pure fold from one trace + the model to a
``CoverageDB`` of bin hits. DBs ``merge`` associatively, so a whole run's coverage is
just the merge over each scenario's per-trace DB. The DB carries its denominator (the
declared bins) so ``pct`` and ``holes`` are unambiguous regardless of what any single
trace happened to hit.
"""

from __future__ import annotations

from itertools import product

from pydantic import BaseModel, Field

from manifold.model import CoverageModel, Coverpoint
from manifold.scenario import Scenario
from manifold.trace import Trace


class CoverageDB(BaseModel):
    hits: dict[str, dict[str, int]] = Field(default_factory=dict)  # group -> bin -> count
    totals: dict[str, list[str]] = Field(default_factory=dict)  # group -> declared bin names

    def merge(self, other: CoverageDB) -> CoverageDB:
        # Preserve declaration order (self's groups first, then other's new ones) so the
        # report is stable run-to-run — a plain set union here would shuffle the groups.
        totals: dict[str, list[str]] = {}
        for src in (self, other):
            for g, bins in src.totals.items():
                totals.setdefault(g, bins)
        hits: dict[str, dict[str, int]] = {}
        for g in totals:
            merged = dict(self.hits.get(g, {}))
            for b, c in other.hits.get(g, {}).items():
                merged[b] = merged.get(b, 0) + c
            hits[g] = merged
        return CoverageDB(hits=hits, totals=totals)

    def hit_total(self, group: str) -> tuple[int, int]:
        bins = self.totals.get(group, [])
        got = self.hits.get(group, {})
        hit = sum(1 for b in bins if got.get(b, 0) > 0)
        return hit, len(bins)

    def pct(self, group: str | None = None) -> float:
        if group is not None:
            hit, total = self.hit_total(group)
            return 100.0 * hit / total if total else 0.0
        hit = total = 0
        for g in self.totals:
            gh, gt = self.hit_total(g)
            hit += gh
            total += gt
        return 100.0 * hit / total if total else 0.0

    def holes(self) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        for g, bins in self.totals.items():
            got = self.hits.get(g, {})
            out.extend((g, b) for b in bins if got.get(b, 0) == 0)
        return out


def _bins_hit(cp: Coverpoint, trace: Trace) -> list[str]:
    values = list(cp.extract(trace))
    return [b.name for b in cp.bins if any(b.predicate(v) for v in values)]


def empty_db(model: CoverageModel) -> CoverageDB:
    """A zero-hit DB carrying the model's declared bins as the denominator — minus any
    bins marked ``ignore`` (structurally unreachable / illegal, à la UVM ignore_bins), so
    the metric never counts a coverpoint you can't actually hit (H4)."""
    totals: dict[str, list[str]] = {}
    for cp in model.coverpoints:
        totals[cp.name] = [b.name for b in cp.bins if b.name not in cp.ignore]
    for tc in model.transitions:
        totals[tc.name] = [f"{a}->{b}" for a, b in tc.edges if f"{a}->{b}" not in tc.ignore]
    for cr in model.crosses:
        a, b = model.coverpoint(cr.a), model.coverpoint(cr.b)
        if a is not None and b is not None:
            cells = [f"{x.name} x {y.name}" for x, y in product(a.bins, b.bins)]
            totals[cr.name] = [c for c in cells if c not in cr.ignore]
    return CoverageDB(hits={g: {} for g in totals}, totals=totals)


def project_bins(model: CoverageModel, scenario: Scenario) -> set[tuple[str, str]]:
    """The (group, bin) coverage a scenario's *inputs* can reach — from each coverpoint's
    optional ``project`` hook, no agent run needed. This is what coverage-directed
    selection scores candidates against (ADR-0004, H1)."""
    reachable: set[tuple[str, str]] = set()
    for cp in model.coverpoints:
        if cp.project is not None:
            reachable.update((cp.name, b) for b in cp.project(scenario))
    for tc in model.transitions:
        if tc.project is not None:
            reachable.update((tc.name, b) for b in tc.project(scenario))
    for cr in model.crosses:
        if cr.project is not None:
            reachable.update((cr.name, b) for b in cr.project(scenario))
    return reachable


def evaluate(trace: Trace, model: CoverageModel) -> CoverageDB:
    db = empty_db(model)

    for cp in model.coverpoints:
        for name in _bins_hit(cp, trace):
            db.hits[cp.name][name] = db.hits[cp.name].get(name, 0) + 1

    for tc in model.transitions:
        seq = tc.symbols(trace)
        seen = set(zip(seq, seq[1:], strict=False))
        for a, b in tc.edges:
            if (a, b) in seen:
                key = f"{a}->{b}"
                db.hits[tc.name][key] = db.hits[tc.name].get(key, 0) + 1

    for cr in model.crosses:
        cpa, cpb = model.coverpoint(cr.a), model.coverpoint(cr.b)
        if cpa is None or cpb is None:
            continue
        for x, y in product(_bins_hit(cpa, trace), _bins_hit(cpb, trace)):
            key = f"{x} x {y}"
            db.hits[cr.name][key] = db.hits[cr.name].get(key, 0) + 1

    return db
