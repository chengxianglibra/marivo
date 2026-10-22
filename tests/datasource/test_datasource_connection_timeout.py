"""Native timeout request validation and connection diagnostics."""

from __future__ import annotations

import pytest

import marivo.datasource as md
from marivo.datasource import backends
from marivo.datasource import manage as manage_mod
from marivo.datasource.ir import DatasourceIR
from marivo.datasource.manage import DEFAULT_CONNECTION_TIMEOUT_SECONDS


def _patch_load_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make ``_store.load_one`` resolve a synthetic datasource (never None)."""
    monkeypatch.setattr(
        manage_mod._store,
        "load_one",
        lambda name, project_root=None: DatasourceIR(
            name=name,
            semantic_id=name,
            backend_type="postgres",
            fields={},
            env_refs={},
            ai_context=None,
            python_symbol=name,
            location=None,
        ),
    )


def test_test_rejects_non_positive_timeout() -> None:
    with pytest.raises(ValueError, match="timeout_seconds must be positive"):
        md.load().test("warehouse", timeout_seconds=0)


def test_test_no_persist_rejects_non_positive_timeout() -> None:
    with pytest.raises(ValueError, match="timeout_seconds must be positive"):
        manage_mod.test_no_persist("warehouse", timeout_seconds=-1)


def test_test_reports_normal_backend_error_as_open_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-timeout connect failure still surfaces as ``connection_open_failed``."""
    _patch_load_one(monkeypatch)

    def failing_build(datasource, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("gateway refused connection")

    monkeypatch.setattr(backends, "build_backend_with_secrets", failing_build)

    result = md.load().test("warehouse", timeout_seconds=1)

    assert result.ok is False
    assert result.failure is not None
    assert result.failure.code == "connection_open_failed"
    assert result.failure.timeout_seconds is None


def test_default_timeout_seconds_is_documented_int() -> None:
    assert DEFAULT_CONNECTION_TIMEOUT_SECONDS == 30
    assert isinstance(DEFAULT_CONNECTION_TIMEOUT_SECONDS, int)
