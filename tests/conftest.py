"""Shared test fixtures: load the example agents module by path (it is not an
installed package), and expose helpers to find error / clean seeds."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

EXAMPLES_DIR = Path(__file__).resolve().parents[1] / "examples"


def _load(name: str) -> ModuleType:
    path = EXAMPLES_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="session")
def toy() -> ModuleType:
    return _load("toy_agents")


@pytest.fixture(scope="session")
def spec() -> ModuleType:
    return _load("spec_example")


@pytest.fixture(scope="session")
def claude() -> ModuleType:
    # Loading this at all proves the module imports with `anthropic` not installed
    # (the live client is imported lazily); the offline demo/test never calls the API.
    return _load("claude_agent")


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
