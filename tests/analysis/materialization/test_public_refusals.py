"""Public refusal deadlines with independent I/O and publication tripwires."""

import os
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.errors import AnalysisError
from marivo.analysis.materialization import graph_store
from marivo.analysis.methods.registry import MethodRegistration, MethodRegistry
from marivo.analysis.methods.semantics import MethodKey
from marivo.analysis.session.core import Session
from marivo.datasource.adapters import SourceSession
from marivo.semantic.reader import SemanticProject
from tests.analysis.graph.reference_fixtures import reference_data
from tests.analysis.graph.source_fixtures import author_source_project
from tests.analysis.materialization.domain_recovery_worker import snapshot
from tests.analysis.materialization.publication_fixtures import publication_counts
from tests.datasource.source_cases import source_case
from tests.shared_fixtures import run_ids
from tests.support.json import Json, encode


def test_public_composition_refuses_before_source_or_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    profile = "table"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    with source_case("duckdb", profile, tmp_path, monkeypatch, reference_data("duckdb")) as case:
        author_source_project("duckdb", case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("r94-refusal", report_timezone="UTC")
        entity = ms.ref.entity("sales.facts")
        measure = ms.ref.measure("sales.facts.amount")
        live = session.members(entity).read(measure)
        previous = live.execute()
        saved = snapshot(previous)
        foreign = mv.session.get_or_create("r94-foreign", report_timezone="UTC")
        other = foreign.members(entity).read(measure)
        published_before = publication_counts(session)
        calls: list[str] = []

        def tripwire(name: str) -> Callable[..., None]:
            def forbidden(*args: object, **kwargs: object) -> None:
                calls.append(name)
                raise AssertionError("Refusal attempted " + name)

            return forbidden

        for name in ("__enter__", "bind", "compile", "batches"):
            monkeypatch.setattr(SourceSession, name, tripwire("source." + name))
        monkeypatch.setattr(graph_store, "admit", tripwire("graph_store.admit"))
        refusals: dict[
            str,
            tuple[
                mv.LogicalNumericRelation | mv.MaterializedNumericRelation,
                mv.LogicalNumericRelation,
                list[Session],
            ],
        ] = {
            "mixed-live-fixed": (live, previous, [session]),
            "cross-session": (live, other, [session, foreign]),
        }
        reports: dict[str, list[Json]] = {}
        for refusal, (left, right, sessions) in refusals.items():
            before = [run_ids(item) for item in sessions]
            errors: list[Json] = []
            for first, second in ((left, right), (right, left)):
                with pytest.raises(AnalysisError) as caught:
                    first.ratio(second).execute()
                error = caught.value
                assert error.expected and error.received and error.repair is not None
                assert error.repair.action
                errors.append(
                    {
                        "type": type(error).__name__,
                        "expected": error.expected,
                        "received": error.received,
                        "repair": error.repair.action,
                    }
                )
            assert calls == []
            assert [run_ids(item) for item in sessions] == before
            assert snapshot(previous) == saved
            assert publication_counts(session) == published_before
            assert all(item._runtime.store.resources(item.id) == () for item in sessions)
            reports[refusal] = errors
        if directory := os.environ.get("MARIVO_R93_EVIDENCE_DIR"):
            for refusal, errors in reports.items():
                Path(directory, "public-refusal-" + refusal + "-" + profile + ".json").write_bytes(
                    encode(
                        {
                            "requirement": "R9:refusal:shared:pre-run-pre-read:refusal:" + refusal,
                            "backend": "duckdb",
                            "profile": profile,
                            "directions": 2,
                            "errors": errors,
                            "tripwire_calls": list(calls),
                            "previous_snapshot": saved,
                            "previous_artifact_preserved": True,
                            "resources": 0,
                            "boundary": "Public ratio composition refusal only; other refusals and physical profiles remain independent.",
                        }
                    )
                )


def test_public_required_part_missing_blocks_recovery_and_exact_hit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    with source_case("duckdb", "table", tmp_path, monkeypatch, reference_data("duckdb")) as case:
        author_source_project("duckdb", case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("r94-part-refusal", report_timezone="UTC")
        values = (
            session.members(ms.ref.entity("sales.facts"))
            .observe(ms.ref.metric("sales.total"), by=(mv.member(),))
            .execute()
        )
        logical = values.ratio(values)
        previous = logical.execute()
        saved = snapshot(previous)
        assert previous._dataset is not None
        descriptor = previous._dataset.artifact.descriptor
        before = run_ids(session)
        published_before = publication_counts(session)
        calls: list[str] = []

        def forbidden(*args: object, **kwargs: object) -> None:
            calls.append("source-or-admission")
            raise AssertionError("Missing required part admitted work")

        for name in ("__enter__", "bind", "compile", "batches"):
            monkeypatch.setattr(SourceSession, name, forbidden)
        monkeypatch.setattr(graph_store, "admit", forbidden)
        reports: dict[str, list[Json]] = {}
        for role in ("current_endpoint", "baseline_endpoint", "correspondence"):
            part = next(item for item in descriptor.parts if item.role == role)
            path = tmp_path / part.local.project_relative_path
            offline = path.with_name(path.name + ".offline")
            path.rename(offline)
            errors: list[Json] = []
            try:
                actions: tuple[Callable[[], object], ...] = (
                    lambda: session.artifact(previous.state.artifact_ref),
                    logical.execute,
                )
                for action in actions:
                    with pytest.raises(AnalysisError) as caught:
                        action()
                    error = caught.value
                    assert error.expected and error.received and error.repair is not None
                    assert error.repair.action
                    errors.append(
                        {
                            "type": type(error).__name__,
                            "expected": error.expected,
                            "received": error.received,
                            "repair": error.repair.action,
                        }
                    )
                assert run_ids(session) == before and calls == []
                assert session._runtime.store.resources(session.id) == ()
            finally:
                offline.rename(path)
            reports[role] = errors
        assert snapshot(previous) == saved
        assert publication_counts(session) == published_before
        if directory := os.environ.get("MARIVO_R93_EVIDENCE_DIR"):
            for role, errors in reports.items():
                Path(directory, "public-refusal-missing-" + role + ".json").write_bytes(
                    encode(
                        {
                            "requirement": "R9:refusal:shared:pre-run-pre-read:refusal:missing-required-parts",
                            "backend": "duckdb",
                            "profile": "table",
                            "role": role,
                            "actions": ["session.artifact", "fixed-exact-hit"],
                            "errors": errors,
                            "tripwire_calls": list(calls),
                            "run_counts_before": [len(before)],
                            "run_counts_after": [len(run_ids(session))],
                            "publication_counts_before": [*published_before],
                            "publication_counts_after": [*publication_counts(session)],
                            "previous_snapshot": saved,
                            "previous_artifact_preserved": True,
                            "resources": 0,
                            "boundary": "Public retained ratio required-part refusal only; other producer/part/profile bindings remain independent.",
                        }
                    )
                )


def test_public_cross_datasource_observation_refuses_before_business_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    from tests.support.source_trace import capture_source

    profile = "table"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    roots = (tmp_path / "left_source", tmp_path / "right_source")
    for root in roots:
        root.mkdir()
    trace = capture_source(monkeypatch)
    with (
        source_case("duckdb", profile, roots[0], monkeypatch, reference_data("duckdb")) as left,
        source_case("duckdb", profile, roots[1], monkeypatch, reference_data("duckdb")) as right,
    ):
        from marivo.datasource.ir import TableSourceIR

        assert isinstance(left.source, TableSourceIR) and isinstance(right.source, TableSourceIR)
        models = "import marivo.semantic as ms\nimport marivo.datasource as md\n"
        for name, case in (("subjects", left), ("events", right)):
            assert isinstance(case.source, TableSourceIR)
            datasource = "left" if name == "subjects" else "right"
            models += f"{name} = ms.entity(name={name!r}, datasource=ms.ref.datasource({datasource!r}), source=md.table({case.source.table!r}), primary_key=['tenant','id','revision'])\n"
            for column in ("tenant", "id", "revision"):
                models += f"{name}_{column} = ms.dimension_column(name={name + '_' + column!r}, entity={name}, column={column!r})\n"
            models += f"{name}_amount = ms.measure_column(name={name + '_amount'!r}, entity={name}, column='amount', additivity=ms.additive_all())\n"
        models += "event_subject = ms.relationship(name='event_subject', from_entity=events, to_entity=subjects, keys=[ms.join_on(events_tenant,subjects_tenant),ms.join_on(events_id,subjects_id),ms.join_on(events_revision,subjects_revision)])\n"
        models += "event_time = ms.time_dimension_column(name='happened', entity=events, column='happened', granularity='second', parse=ms.timestamp(timezone='UTC'), is_default=True)\n"
        models += "total = ms.aggregate(name='total', measure=events_amount, agg='sum', time=event_time, empty=ms.empty.zero())\n"
        semantic_project_factory(
            {
                "datasources/left.py": "import marivo.datasource as md\n"
                + f"md.duckdb(name='left', path={str(roots[0] / 'source.duckdb')!r}, read_only=True)\n",
                "datasources/right.py": "import marivo.datasource as md\n"
                + f"md.duckdb(name='right', path={str(roots[1] / 'source.duckdb')!r}, read_only=True)\n",
                "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales', owner='R9', default=True)\n",
                "sales/models.py": models,
            }
        )
        session = mv.session.get_or_create("r94-cross-datasource", report_timezone="UTC")
        members = session.members(ms.ref.entity("sales.subjects"))
        previous = members.read(ms.ref.measure("sales.subjects.subjects_amount")).execute()
        saved = snapshot(previous)
        before = run_ids(session)
        published_before = publication_counts(session)
        native_before = list(trace.native_sql)
        calls: list[str] = []

        def forbidden(*args: object, **kwargs: object) -> None:
            calls.append("business-read-or-admission")
            raise AssertionError("Cross-datasource graph admitted work")

        for name in ("compile", "batches"):
            monkeypatch.setattr(SourceSession, name, forbidden)
        monkeypatch.setattr(graph_store, "admit", forbidden)
        with pytest.raises(AnalysisError) as caught:
            members.observe(
                ms.ref.metric("sales.total"),
                via=ms.ref.relationship("sales.event_subject"),
                by=(mv.member(),),
            ).execute()
        error = caught.value
        assert error.expected == "one selected datasource"
        assert error.received == "['left', 'right']"
        assert error.expected and error.received and error.repair is not None
        assert error.repair.action
        assert calls == [] and trace.native_sql == native_before
        assert run_ids(session) == before and publication_counts(session) == published_before
        assert session._runtime.store.resources(session.id) == ()
        assert snapshot(previous) == saved
        if directory := os.environ.get("MARIVO_R93_EVIDENCE_DIR"):
            Path(directory, "public-refusal-cross-datasource-" + profile + ".json").write_bytes(
                encode(
                    {
                        "requirement": "R9:refusal:shared:pre-run-pre-read:refusal:cross-datasource",
                        "profile": profile,
                        "datasources": ["left", "right"],
                        "distinct_physical_files": True,
                        "error": {
                            "type": type(error).__name__,
                            "expected": error.expected,
                            "received": error.received,
                            "repair": error.repair.action,
                        },
                        "tripwire_calls": list(calls),
                        "native_business_submissions_after": len(trace.native_sql)
                        - len(native_before),
                        "run_counts_before": [len(before)],
                        "run_counts_after": [len(run_ids(session))],
                        "publication_counts_before": [*published_before],
                        "publication_counts_after": [*publication_counts(session)],
                        "previous_artifact_preserved": True,
                        "previous_snapshot": saved,
                        "resources": 0,
                        "boundary": "Public two-DuckDB datasource relationship observation only; R1 metadata remains allowed, business reads and new Runs are forbidden.",
                    }
                )
            )


def test_public_missing_sqlite_qualification_refuses_before_business_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    from tests.support.source_trace import capture_source

    profile = "table"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    trace = capture_source(monkeypatch)
    with source_case("sqlite", profile, tmp_path, monkeypatch, reference_data("sqlite")) as case:
        author_source_project(
            "sqlite", case, monkeypatch, semantic_project_factory, read_timezone="America/New_York"
        )
        session = mv.session.get_or_create("r94-missing-qualification", report_timezone="UTC")
        members = session.members(ms.ref.entity("sales.facts"))
        previous = members.read(ms.ref.measure("sales.facts.amount")).execute()
        saved = snapshot(previous)
        before = run_ids(session)
        published_before = publication_counts(session)
        native_before = list(trace.native_sql)
        calls: list[str] = []

        lookup = MethodRegistry.lookup

        def missing(registry: MethodRegistry, key: MethodKey) -> MethodRegistration:
            registration = lookup(registry, key)
            return (
                replace(registration, implementations=())
                if key.name == "metric.sum_zero"
                else registration
            )

        def forbidden(*args: object, **kwargs: object) -> None:
            calls.append("business-read-or-admission")
            raise AssertionError("Unqualified source route admitted work")

        for name in ("compile", "batches"):
            monkeypatch.setattr(SourceSession, name, forbidden)
        monkeypatch.setattr(MethodRegistry, "lookup", missing)
        monkeypatch.setattr(graph_store, "admit", forbidden)
        with pytest.raises(AnalysisError) as caught:
            members.observe(
                ms.ref.metric("sales.total"),
                during=mv.time_scope(start="2026-08-01", end="2026-08-02"),
                by=(mv.member(),),
            ).execute()
        error = caught.value
        assert "qualified exact key for metric.sum_zero" in error.expected
        assert error.expected and error.repair is not None and error.repair.action
        assert calls == [] and trace.native_sql == native_before
        assert run_ids(session) == before and publication_counts(session) == published_before
        assert session._runtime.store.resources(session.id) == ()
        assert snapshot(previous) == saved
        if directory := os.environ.get("MARIVO_R93_EVIDENCE_DIR"):
            Path(directory, "public-refusal-no-static-route-" + profile + ".json").write_bytes(
                encode(
                    {
                        "requirement": "R9:refusal:shared:pre-run-pre-read:refusal:no-static-route",
                        "backend": "sqlite",
                        "profile": profile,
                        "read_timezone": "America/New_York",
                        "error": {
                            "type": type(error).__name__,
                            "expected": error.expected,
                            "received": error.received,
                            "repair": error.repair.action,
                        },
                        "tripwire_calls": list(calls),
                        "native_business_submissions_after": len(trace.native_sql)
                        - len(native_before),
                        "run_counts_before": [len(before)],
                        "run_counts_after": [len(run_ids(session))],
                        "publication_counts_before": [*published_before],
                        "publication_counts_after": [*publication_counts(session)],
                        "previous_artifact_preserved": True,
                        "previous_snapshot": saved,
                        "resources": 0,
                        "boundary": "Missing exact SQLite sum qualification after UTC normalization; R1 metadata remains allowed. No fallback, source business read or new Run is admitted.",
                    }
                )
            )
