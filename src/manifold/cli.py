"""Manifold CLI (DESIGN.md §6.8).

    manifold run   <agent_file> [--spec S] [--agent NAME] [--scenarios N] [--seed B]
                                [--repeats K] [--html PATH]
    manifold repro <seed> <agent_file> [--spec S] [--agent NAME]
    manifold cover <spec_file>

``run`` sweeps N seeded scenarios (each ``--repeats`` times), measures functional
coverage, and reports holes, flaky seeds, and invariant failures with a one-line repro.
``repro`` replays one seed. ``cover`` lists a declared coverage model. M3 adds the
coverage-directed sweep on top of this uniform one.

A target module exposes ``AGENTS: dict[str, Agent]`` and either ``SPACE: ScenarioSpace``
(the M2 generator) or ``make_scenario(seed)`` (the M0 fallback). A spec module (or the
target module) may expose ``MODEL: CoverageModel`` and ``INVARIANTS``; absent a model, a
generic default is used.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Callable
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from manifold.agent import Agent
from manifold.closure import sweep
from manifold.generate import ScenarioSpace, sample
from manifold.harness import run as run_agent
from manifold.invariants import Invariant, no_infinite_retry, terminates_within_budget
from manifold.model import CoverageModel, default_model
from manifold.report import coverage_table, write_html
from manifold.scenario import Scenario
from manifold.trace import StateSnapshot, ToolCall, ToolResult, Trace

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Functional coverage + constrained-random verification for AI agents.",
)
console = Console()

# The M0 default invariants, used when no spec/module provides its own.
DEFAULT_INVARIANTS: list[Invariant] = [terminates_within_budget, no_infinite_retry()]


def _load_module(path: str) -> object:
    p = Path(path).resolve()
    if not p.exists():
        console.print(f"[red]No such file:[/] {path}")
        raise typer.Exit(2)
    spec = importlib.util.spec_from_file_location(p.stem, p)
    if spec is None or spec.loader is None:
        console.print(f"[red]Cannot import:[/] {path}")
        raise typer.Exit(2)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _resolve_agent(mod: object, name: str | None) -> Agent:
    agents: dict[str, Agent] = getattr(mod, "AGENTS", {})
    if not agents:
        console.print("[red]Module exposes no AGENTS dict.[/]")
        raise typer.Exit(2)
    if name is None:
        if len(agents) == 1:
            return next(iter(agents.values()))
        console.print(f"[yellow]Multiple agents; pick one with --agent:[/] {', '.join(agents)}")
        raise typer.Exit(2)
    if name not in agents:
        console.print(f"[red]Unknown agent {name!r}.[/] Available: {', '.join(agents)}")
        raise typer.Exit(2)
    return agents[name]


def _scenario_builder(spec_mod: object | None, agent_mod: object) -> Callable[[int], Scenario]:
    """Prefer the M2 generator (a SPACE) over the M0 hand-built make_scenario."""
    for src in (spec_mod, agent_mod):
        space = getattr(src, "SPACE", None)
        if isinstance(space, ScenarioSpace):
            sp = space
            return lambda seed: sample(sp, seed)
    for src in (spec_mod, agent_mod):
        fn = getattr(src, "make_scenario", None)
        if callable(fn):
            builder: Callable[[int], Scenario] = fn
            return builder
    console.print("[red]Module exposes neither SPACE (a ScenarioSpace) nor make_scenario(seed).[/]")
    raise typer.Exit(2)


def _resolve_model(spec_mod: object | None, agent_mod: object) -> CoverageModel:
    for src in (spec_mod, agent_mod):
        model = getattr(src, "MODEL", None)
        if isinstance(model, CoverageModel):
            return model
    return default_model()


def _resolve_invariants(spec_mod: object | None, agent_mod: object) -> list[Invariant]:
    for src in (spec_mod, agent_mod):
        invs = getattr(src, "INVARIANTS", None)
        if invs:
            return list(invs)
    return DEFAULT_INVARIANTS


def _inv_name(inv: Invariant) -> str:
    return getattr(inv, "__name__", None) or "invariant"


def _describe(ev: object) -> str:
    if isinstance(ev, ToolCall):
        return f"{ev.tool}({', '.join(f'{k}={v!r}' for k, v in ev.args.items())})"
    if isinstance(ev, ToolResult):
        return f"{ev.tool} -> {'ok' if ev.ok else f'FAULT:{ev.fault}'}"
    if isinstance(ev, StateSnapshot):
        return ev.label
    return str(getattr(ev, "detail", getattr(ev, "text", getattr(ev, "reason", ""))))


@app.command()
def run(
    agent_file: str = typer.Argument(..., help="Module exposing AGENTS + SPACE/make_scenario."),
    spec: str | None = typer.Option(None, "--spec", help="Module exposing MODEL + INVARIANTS."),
    agent: str | None = typer.Option(None, "--agent", "-a", help="Agent id (if module has >1)."),
    scenarios: int = typer.Option(10, "--scenarios", "-n", help="Number of seeds to sweep."),
    seed: int = typer.Option(0, "--seed", "-s", help="Base seed."),
    repeats: int = typer.Option(1, "--repeats", "-k", help="Runs per seed; >1 surfaces flakiness."),
    coverage_directed: bool = typer.Option(
        False, "--coverage-directed/--random", help="Bias generation toward coverage holes."
    ),
    html: str | None = typer.Option(None, "--html", help="Write a static HTML coverage report."),
) -> None:
    """Sweep N seeded scenarios, measure coverage, and report holes + flaky seeds + failures."""
    mod = _load_module(agent_file)
    spec_mod = _load_module(spec) if spec else None
    ag = _resolve_agent(mod, agent)
    model = _resolve_model(spec_mod, mod)
    invariants = _resolve_invariants(spec_mod, mod)
    build = _scenario_builder(spec_mod, mod)

    result = sweep(
        ag, build, model, invariants,
        scenarios=scenarios, base_seed=seed, repeats=repeats,
        coverage_directed=coverage_directed,
    )
    spec_arg = f" --spec {spec}" if spec else ""

    rep = f" x {repeats} repeats" if repeats > 1 else ""
    mode = "coverage-directed" if coverage_directed else "random"
    console.print(
        f"\n[bold]Manifold[/] · {scenarios} scenarios{rep} · {mode} · agent=[cyan]{ag.id}[/]"
    )
    console.print(coverage_table(result.db))
    fully = result.fully_passing()
    console.print(f"\nSeeds fully passing every invariant: [bold]{fully}/{result.n_scenarios}[/]")

    flaky = result.flaky_seeds()
    if flaky:
        ft = Table(title=f"Flaky seeds ({len(flaky)}) — pass on some repeats, fail on others")
        ft.add_column("seed")
        ft.add_column("passed", justify="right")
        ft.add_column("repro")
        for s in flaky[:12]:
            flags = result.results_by_seed[s]
            repro_cmd = f"manifold repro {s} {agent_file} --agent {ag.id}{spec_arg}"
            ft.add_row(f"0x{s:x}", f"{sum(flags)}/{len(flags)}", repro_cmd)
        console.print(ft)

    failures = result.failures
    if failures:
        cap = 12
        title = f"Failures ({len(failures)})" + (f" — first {cap}" if len(failures) > cap else "")
        table = Table(title=title)
        table.add_column("invariant", style="red")
        table.add_column("seed")
        table.add_column("detail")
        for f in failures[:cap]:
            table.add_row(f.invariant, f"0x{f.seed:x}", f.detail)
        console.print(table)
        if len(failures) > cap:
            console.print(f"[dim]… +{len(failures) - cap} more (see the HTML report or repro).[/]")
        ex = failures[0]
        console.print(
            f"\nReproduce: [bold]manifold repro {ex.seed} {agent_file} --agent {ag.id}{spec_arg}[/]"
        )

    if html:
        write_html(result.db, failures, html, title=f"Manifold · {ag.id}")
        console.print(f"HTML report -> [bold]{html}[/]")

    if failures:
        raise typer.Exit(1)
    console.print("[green]All scenarios satisfied every invariant.[/]")


@app.command()
def repro(
    seed: int = typer.Argument(..., help="The scenario seed to reproduce."),
    agent_file: str = typer.Argument(..., help="Module exposing AGENTS + SPACE/make_scenario."),
    spec: str | None = typer.Option(None, "--spec", help="Module exposing INVARIANTS."),
    agent: str | None = typer.Option(None, "--agent", "-a", help="Agent id (if module has >1)."),
) -> None:
    """Re-run one scenario by seed and print its full trace + verdict."""
    mod = _load_module(agent_file)
    spec_mod = _load_module(spec) if spec else None
    ag = _resolve_agent(mod, agent)
    invariants = _resolve_invariants(spec_mod, mod)
    build = _scenario_builder(spec_mod, mod)
    trace: Trace = run_agent(ag, build(seed))

    table = Table(title=f"Trace · seed=0x{seed:x} · agent={ag.id}")
    table.add_column("step", justify="right")
    table.add_column("event")
    table.add_column("detail")
    for ev in trace.events:
        table.add_row(str(ev.step), ev.kind, _describe(ev))
    console.print(table)

    console.print("\n[bold]Invariants[/]")
    any_failed = False
    for inv in invariants:
        v = inv(trace)
        if v is None:
            console.print(f"  [green]PASS[/] {_inv_name(inv)}")
        else:
            any_failed = True
            console.print(f"  [red]FAIL[/] {v.invariant} — {v.detail}")
    if any_failed:
        raise typer.Exit(1)


@app.command()
def cover(
    spec_file: str = typer.Argument(..., help="Module exposing MODEL (a CoverageModel)."),
) -> None:
    """List a declared coverage model — a sanity check on what you're measuring."""
    mod = _load_module(spec_file)
    model = getattr(mod, "MODEL", None)
    if not isinstance(model, CoverageModel):
        console.print("[yellow]No MODEL in module; showing the generic default model.[/]")
        model = default_model()

    cp_table = Table(title="Coverpoints")
    cp_table.add_column("coverpoint")
    cp_table.add_column("bins")
    for cp in model.coverpoints:
        cp_table.add_row(cp.name, ", ".join(b.name for b in cp.bins))
    console.print(cp_table)

    if model.transitions:
        tr_table = Table(title="Transition coverpoints (edges)")
        tr_table.add_column("coverpoint")
        tr_table.add_column("edges")
        for tc in model.transitions:
            tr_table.add_row(tc.name, ", ".join(f"{a}->{b}" for a, b in tc.edges))
        console.print(tr_table)

    if model.crosses:
        cx_table = Table(title="Crosses")
        cx_table.add_column("cross")
        cx_table.add_column("a x b")
        cx_table.add_column("cells", justify="right")
        for cr in model.crosses:
            a, b = model.coverpoint(cr.a), model.coverpoint(cr.b)
            cells = len(a.bins) * len(b.bins) if a and b else 0
            cx_table.add_row(cr.name, f"{cr.a} x {cr.b}", str(cells))
        console.print(cx_table)


if __name__ == "__main__":  # pragma: no cover
    app()
