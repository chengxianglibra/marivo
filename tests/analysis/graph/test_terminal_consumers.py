"""Native public C01.c terminal reads; no typed analysis qualification."""

import json
import os
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import ibis
import pytest
from ibis.backends import BaseBackend

import marivo.datasource as md
import marivo.semantic as ms
from marivo.datasource.adapters import PhysicalRequirement
from marivo.datasource.errors import DatasourceRawSqlError, DatasourceSourceCapabilityError
from marivo.datasource.runtime import DatasourceConnectionService
from marivo.semantic.reader import SemanticProject
from tests.analysis.graph.source_fixtures import author_source_project
from tests.datasource.source_cases import ROWS, source_case


@pytest.mark.runtime
@pytest.mark.parametrize(
    "backend", ("duckdb", "sqlite", "postgres", "mysql", "trino", "clickhouse")
)
def test_terminal_native_timeout_is_not_analysis_cancellation(
    backend: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    submitted: list[str] = []
    disconnected: list[bool] = []
    original = DatasourceConnectionService.use_backend

    @contextmanager
    def traced(
        service: DatasourceConnectionService,
        name: str,
        *,
        read_only: bool = False,
        terminal_timeout_seconds: int | None = None,
        on_disconnect: Callable[[bool], None] | None = None,
    ) -> Iterator[BaseBackend]:
        def released(confirmed: bool) -> None:
            disconnected.append(confirmed)
            if on_disconnect is not None:
                on_disconnect(confirmed)

        with original(
            service,
            name,
            read_only=read_only,
            terminal_timeout_seconds=terminal_timeout_seconds,
            on_disconnect=released,
        ) as owner:
            raw = owner.raw_sql

            def execute(statement: str, **kwargs: object) -> object:
                submitted.append(statement)
                return raw(statement, **kwargs)

            monkeypatch.setattr(owner, "raw_sql", execute)
            yield owner

    profile = {"trino": "iceberg", "clickhouse": "mergetree"}.get(backend, "table")
    with source_case(backend, profile, tmp_path, monkeypatch) as case:
        author_source_project(backend, case, monkeypatch, semantic_project_factory)
        bound = case.session.bind(case.source, source_identity="r93.c01.terminal.timeout")
        expression = bound.relation
        for _ in range(18):
            expression = expression.cross_join(bound.relation.view()).select(expression)
        expression = expression.mutate(noise=ibis.random()).aggregate(value=lambda t: t.noise.sum())
        qualified = case.session.qualify(
            bound, PhysicalRequirement("basic.rows", 1, frozenset({"scan", "project"}))
        )
        statement = case.session.compile(
            qualified,
            expression,
            purpose="r93.terminal.timeout.oracle",
            expected_schema=expression.schema().to_pyarrow(),
        ).sql
        monkeypatch.setattr(DatasourceConnectionService, "use_backend", traced)
        started = time.monotonic()
        with pytest.raises(DatasourceRawSqlError) as refused:
            md.raw_sql(
                ms.ref.datasource("warehouse"),
                statement,
                reason="verify terminal-owned native timeout on an expensive generated SELECT",
                limit=1,
                timeout_seconds=1,
                project_root=tmp_path,
            )
        elapsed = time.monotonic() - started
        assert 0.9 < elapsed < 5
        assert refused.value.effect_observed is not None
        assert refused.value.effect_observed.query_executed is True
        cause = refused.value.__cause__
        assert cause is not None
        if backend == "postgres":
            assert getattr(cause, "sqlstate", None) == "57014"
        elif backend == "mysql":
            assert "2013" in str(cause) or "1317" in str(cause)
        elif backend == "trino":
            assert getattr(cause, "error_name", None) == "EXCEEDED_TIME_LIMIT"
        elif backend == "clickhouse":
            assert "159" in str(cause) and "TIMEOUT_EXCEEDED" in str(cause)
        else:
            assert "interrupt" in str(cause).lower()
        assert submitted == [statement]
        assert disconnected == [True]
        assert not case.session.submissions
        receipt = {
            "backend": backend,
            "environment": case.environment,
            "timeout_seconds": 1,
            "elapsed_seconds": elapsed,
            "actual_backend_submissions": submitted,
            "native_error": type(cause).__name__,
            "native_sqlstate": getattr(cause, "sqlstate", None),
            "native_error_name": getattr(cause, "error_name", None),
            "connection_disconnect_acknowledgements": disconnected,
            "typed_analysis_submissions": 0,
            "boundary": "Terminal-owned native timeout only; independent remote termination, fetch races and Analysis cancellation are separate",
        }
    if directory := os.environ.get("MARIVO_R93_EVIDENCE_DIR"):
        Path(directory, f"c01-terminal-timeout-{backend}.json").write_text(
            json.dumps(receipt, sort_keys=True)
        )


@pytest.mark.runtime
@pytest.mark.parametrize(
    "backend", ("duckdb", "sqlite", "postgres", "mysql", "trino", "clickhouse")
)
def test_terminal_exact_rows_and_typed_reentry_refusal(
    backend: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    profile = {"trino": "iceberg", "clickhouse": "mergetree"}.get(backend, "table")
    submitted: list[str] = []
    disconnected: list[bool] = []
    original = DatasourceConnectionService.use_backend

    @contextmanager
    def traced(
        service: DatasourceConnectionService,
        name: str,
        *,
        read_only: bool = False,
        terminal_timeout_seconds: int | None = None,
        on_disconnect: Callable[[bool], None] | None = None,
    ) -> Iterator[BaseBackend]:
        def released(confirmed: bool) -> None:
            disconnected.append(confirmed)
            if on_disconnect is not None:
                on_disconnect(confirmed)

        with original(
            service,
            name,
            read_only=read_only,
            terminal_timeout_seconds=terminal_timeout_seconds,
            on_disconnect=released,
        ) as owner:
            assert isinstance(owner, BaseBackend)
            raw = owner.raw_sql

            def execute(statement: str, **kwargs: object) -> object:
                submitted.append(statement)
                return raw(statement, **kwargs)

            monkeypatch.setattr(owner, "raw_sql", execute)
            yield owner

    with source_case(backend, profile, tmp_path, monkeypatch) as case:
        arguments = {
            **case.session.datasource.fields,
            **{key + "_env": value for key, value in case.session.datasource.env_refs.items()},
        }
        if "user" in arguments:
            monkeypatch.setenv("MARIVO_R93_TERMINAL_USER", str(arguments.pop("user")))
            arguments["user_env"] = "MARIVO_R93_TERMINAL_USER"
        semantic_project_factory(
            {
                "datasources/terminal.py": "import marivo.datasource as md\n"
                + f"md.{backend}(name='terminal',"
                + ",".join(f"{key}={value!r}" for key, value in arguments.items())
                + ")\n",
                "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales',owner='R9',default=True)\n",
            }
        )
        bound = case.session.bind(case.source, source_identity="r93.c01.terminal")
        qualified = case.session.qualify(
            bound, PhysicalRequirement("basic.rows", 1, frozenset({"scan", "project"}))
        )
        expression = bound.relation.order_by("tenant", "id", "revision")
        statement = case.session.compile(
            qualified,
            expression,
            purpose="r93.terminal.oracle",
            expected_schema=expression.schema().to_pyarrow(),
        ).sql
        monkeypatch.setattr(DatasourceConnectionService, "use_backend", traced)
        observations: list[dict[str, object]] = []
        for limit, expected, truncated in ((1, ROWS[:1], True), (3, ROWS, False)):
            result = md.raw_sql(
                ms.ref.datasource("terminal"),
                statement,
                reason="verify terminal exact identity and truncation",
                limit=limit,
                timeout_seconds=5,
                project_root=tmp_path,
            )
            assert list(result.rows) == expected
            assert result.is_truncated is truncated
            assert result.returned_row_count == limit
            assert result.timeout_seconds == 5
            assert not hasattr(result, "contract") and not hasattr(result, "execute")
            before = len(case.session.submissions)
            with pytest.raises(DatasourceSourceCapabilityError):
                # Deliberately violate the annotation to verify runtime terminal isolation.
                case.session.bind(result, source_identity="forbidden-terminal-reentry")  # type: ignore[arg-type]
            assert len(case.session.submissions) == before == 0
            observations.append(
                {"limit": limit, "rows": list(result.rows), "is_truncated": result.is_truncated}
            )
        assert submitted.count(statement) == 2
        assert disconnected == [True, True]
        receipt = {
            "backend": backend,
            "profile": profile,
            "environment": case.environment,
            "oracle": ROWS,
            "observations": observations,
            "actual_backend_submissions": submitted,
            "terminal_sql": statement,
            "connection_disconnected": disconnected,
            "typed_reentry_refused": True,
            "typed_business_submissions": 0,
            "boundary": "Terminal public read only; independent timeout/cancellation proof remains required",
        }
    assert case.session._closed
    if directory := os.environ.get("MARIVO_R93_EVIDENCE_DIR"):
        Path(directory, f"c01-terminal-{backend}.json").write_text(
            json.dumps(receipt, sort_keys=True)
        )
