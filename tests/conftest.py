"""Shared test fixtures: load the example agents module by path (it is not an
installed package), and expose helpers to find error / clean seeds."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "toy_agents.py"


def _load_toy() -> ModuleType:
    spec = importlib.util.spec_from_file_location("toy_agents", EXAMPLES)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["toy_agents"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="session")
def toy() -> ModuleType:
    return _load_toy()


@pytest.fixture(scope="session")
def error_seed(toy: ModuleType) -> int:
    """A seed whose scenario injects a persistent `search` outage."""
    for s in range(500):
        if toy.make_scenario(s).mocks[0].faults:
            return s
    raise AssertionError("no error-injecting seed found in range")


@pytest.fixture(scope="session")
def clean_seed(toy: ModuleType) -> int:
    """A seed whose scenario injects no fault."""
    for s in range(500):
        if not toy.make_scenario(s).mocks[0].faults:
            return s
    raise AssertionError("no clean seed found in range")
