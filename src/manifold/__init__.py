"""Manifold — functional coverage and constrained-random verification for AI agents.

Public surface stabilises as milestones land; for now the building blocks are:

    from manifold.agent import Agent, ToolEnv
    from manifold.scenario import Scenario, Budgets, ToolMock, Fault
    from manifold.trace import Trace
    from manifold.harness import run
    from manifold.invariants import terminates_within_budget, no_infinite_retry

See DESIGN.md for the full picture.
"""

__version__ = "0.0.3"
