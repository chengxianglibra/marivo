from __future__ import annotations

import importlib
import sys

import pytest

from app.core.intent.intent_registry import IntentRunnerRegistry
from app.core.intent.step_registry import StepRunnerRegistry

# ---------------------------------------------------------------------------
# No-I/O import checks
# ---------------------------------------------------------------------------


def test_step_registry_has_no_io_imports() -> None:
    mod_name = "app.core.intent.step_registry"
    if mod_name in sys.modules:
        del sys.modules[mod_name]

    mod = importlib.import_module(mod_name)

    forbidden = {
        "sqlalchemy",
        "asyncio",
        "httpx",
        "aiohttp",
        "requests",
        "app.service",
        "app.infrastructure",
        "app.analysis_core",
    }

    for attr_name in dir(mod):
        obj = getattr(mod, attr_name)
        module = getattr(obj, "__module__", None)
        if module and any(module.startswith(f) or module == f for f in forbidden):
            pytest.fail(
                f"app.core.intent.step_registry references I/O module {module!r} "
                f"via attribute {attr_name!r}"
            )


def test_intent_registry_has_no_io_imports() -> None:
    mod_name = "app.core.intent.intent_registry"
    if mod_name in sys.modules:
        del sys.modules[mod_name]

    mod = importlib.import_module(mod_name)

    forbidden = {
        "sqlalchemy",
        "asyncio",
        "httpx",
        "aiohttp",
        "requests",
        "app.service",
        "app.infrastructure",
        "app.analysis_core",
    }

    for attr_name in dir(mod):
        obj = getattr(mod, attr_name)
        module = getattr(obj, "__module__", None)
        if module and any(module.startswith(f) or module == f for f in forbidden):
            pytest.fail(
                f"app.core.intent.intent_registry references I/O module {module!r} "
                f"via attribute {attr_name!r}"
            )


# ---------------------------------------------------------------------------
# StepRunnerRegistry functional tests
# ---------------------------------------------------------------------------


def test_step_runner_register_and_get() -> None:
    registry = StepRunnerRegistry()

    def runner(session_id: str, params: dict | None = None) -> dict:
        return {"session_id": session_id, "params": params}

    registry.register("metric_query", runner)
    assert registry.get("metric_query") is runner


def test_step_runner_get_unknown_raises() -> None:
    registry = StepRunnerRegistry()
    with pytest.raises(KeyError, match="Unknown step runner"):
        registry.get("nonexistent")


def test_step_runner_run() -> None:
    registry = StepRunnerRegistry()

    def runner(session_id: str, params: dict | None = None) -> dict:
        return {"session_id": session_id, "ran": True}

    registry.register("metric_query", runner)
    result = registry.run("s1", "metric_query", {"key": "val"})
    assert result == {"session_id": "s1", "ran": True}


def test_step_runner_keys_and_supported() -> None:
    registry = StepRunnerRegistry()

    def dummy(session_id: str, params: dict | None = None) -> dict:
        return {}

    registry.register("metric_query", dummy)
    registry.register("profile_table", dummy)

    assert set(registry.keys()) == {"metric_query", "profile_table"}
    assert registry.supported_step_types() == ("metric_query", "profile_table")


def test_step_runner_case_insensitive() -> None:
    registry = StepRunnerRegistry()

    def runner(session_id: str, params: dict | None = None) -> dict:
        return {}

    registry.register("Metric_Query", runner)
    assert registry.get("metric_query") is runner


# ---------------------------------------------------------------------------
# IntentRunnerRegistry functional tests
# ---------------------------------------------------------------------------


def test_intent_runner_register_and_get() -> None:
    registry = IntentRunnerRegistry()

    def runner(session_id: str, params: dict | None = None) -> dict:
        return {"session_id": session_id}

    registry.register("observe", runner)
    assert registry.get("observe") is runner


def test_intent_runner_get_unknown_raises() -> None:
    registry = IntentRunnerRegistry()
    with pytest.raises(KeyError, match="Unknown intent runner"):
        registry.get("nonexistent")


def test_intent_runner_run() -> None:
    registry = IntentRunnerRegistry()

    def runner(session_id: str, params: dict | None = None) -> dict:
        return {"session_id": session_id, "intent": True}

    registry.register("observe", runner)
    result = registry.run("s1", "observe")
    assert result == {"session_id": "s1", "intent": True}


def test_intent_runner_keys_and_supported() -> None:
    registry = IntentRunnerRegistry()

    def dummy(session_id: str, params: dict | None = None) -> dict:
        return {}

    registry.register("observe", dummy)
    registry.register("compare", dummy)

    assert set(registry.keys()) == {"observe", "compare"}
    assert registry.supported_intent_types() == ("compare", "observe")


def test_intent_runner_case_insensitive() -> None:
    registry = IntentRunnerRegistry()

    def runner(session_id: str, params: dict | None = None) -> dict:
        return {}

    registry.register("Observe", runner)
    assert registry.get("observe") is runner
