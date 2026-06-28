"""Manifold CLI (DESIGN.md §6.8).

M0 ships two commands:

    manifold run   <agent_file> [--agent NAME] [--scenarios N] [--seed BASE]
    manifold repro <seed> <agent_file> [--agent NAME]

``run`` sweeps N seeded scenarios and reports invariant failures with a one-line
repro for each. ``repro`` re-runs a single seed and prints its full trace + verdict.
Coverage reporting (the heatmap) lands in M1; constrained-random generation in M2.

An agent module must expose ``AGENTS: dict[str, Agent]`` and ``make_scenario(seed)``.
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
from manifold.harness import run as run_agent
from manifold.invariants import (
    Invariant,
    Violation,
    no_infinite_retry,
    terminates_within_budget,
)
from manifold.scenario import Scenario
from manifold.trace import StateSnapshot, ToolCall, ToolResult, Trace

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Functional coverage + constrained-random verification for AI agents.",
)
console = Console()

# Named so the verdict table can show passing invariants too.
NAMED_INVARIANTS: list[tuple[str, Invariant]] = [
    ("terminates_within_budget", terminates_within_budget),
    ("no_infinite_retry", no_infinite_retry()),
]


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


def _make_scenario(mod: object, seed: int) -> Scenario:
    fn: Callable[[int], Scenario] | None = getattr(mod, "make_scenario", None)
    if fn is None:
        console.print("[red]Module exposes no make_scenario(seed).[/]")
        raise typer.Exit(2)
    return fn(seed)


def _describe(ev: object) -> str:
    if isinstance(ev, ToolCall):
        return f"{ev.tool}({', '.join(f'{k}={v!r}' for k, v in ev.args.items())})"
    if isinstance(ev, ToolResult):
        status = "ok" if ev.ok else f"FAULT:{ev.fault}"
        return f"{ev.tool} -> {status}"
    if isinstance(ev, StateSnapshot):
        return ev.label
    return getattr(ev, "detail", getattr(ev, "text", getattr(ev, "reason", "")))


@app.command()
def run(
    agent_file: str = typer.Argument(..., help="Module exposing AGENTS + make_scenario."),
    agent: str | None = typer.Option(None, "--agent", "-a", help="Agent id (if module has >1)."),
    scenarios: int = typer.Option(10, "--scenarios", "-n", help="Number of seeds to sweep."),
    seed: int = typer.Option(0, "--seed", "-s", help="Base seed."),
) -> None:
    """Sweep N seeded scenarios against the agent and report invariant failures."""
    mod = _load_module(agent_file)
    ag = _resolve_agent(mod, agent)

    failures: list[Violation] = []
    for i in range(scenarios):
        trace = run_agent(ag, _make_scenario(mod, seed + i))
        for _, inv in NAMED_INVARIANTS:
            v = inv(trace)
            if v is not None:
                failures.append(v)

    failing_seeds = {f.seed for f in failures}
    passed = scenarios - len(failing_seeds)
    console.print(f"\n[bold]Manifold[/] · {scenarios} scenarios · agent=[cyan]{ag.id}[/]")
    console.print(f"Scenarios satisfying every invariant: [bold]{passed}/{scenarios}[/]\n")

    if failures:
        table = Table(title=f"Failures ({len(failures)})")
        table.add_column("invariant", style="red")
        table.add_column("seed")
        table.add_column("detail")
        for f in failures:
            table.add_row(f.invariant, f"0x{f.seed:x}", f.detail)
        console.print(table)
        ex = failures[0]
        console.print(
            f"\nReproduce: [bold]manifold repro {ex.seed} {agent_file} --agent {ag.id}[/]"
        )
        raise typer.Exit(1)
    console.print("[green]All scenarios satisfied every invariant.[/]")


@app.command()
def repro(
    seed: int = typer.Argument(..., help="The scenario seed to reproduce."),
    agent_file: str = typer.Argument(..., help="Module exposing AGENTS + make_scenario."),
    agent: str | None = typer.Option(None, "--agent", "-a", help="Agent id (if module has >1)."),
) -> None:
    """Re-run one scenario by seed and print its full trace + verdict."""
    mod = _load_module(agent_file)
    ag = _resolve_agent(mod, agent)
    trace: Trace = run_agent(ag, _make_scenario(mod, seed))

    table = Table(title=f"Trace · seed=0x{seed:x} · agent={ag.id}")
    table.add_column("step", justify="right")
    table.add_column("event")
    table.add_column("detail")
    for ev in trace.events:
        table.add_row(str(ev.step), ev.kind, _describe(ev))
    console.print(table)

    console.print("\n[bold]Invariants[/]")
    any_failed = False
    for name, inv in NAMED_INVARIANTS:
        v = inv(trace)
        if v is None:
            console.print(f"  [green]PASS[/] {name}")
        else:
            any_failed = True
            console.print(f"  [red]FAIL[/] {name} — {v.detail}")
    if any_failed:
        raise typer.Exit(1)


if __name__ == "__main__":  # pragma: no cover
    app()
