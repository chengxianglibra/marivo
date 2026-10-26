"""Public refusal deadlines with independent I/O and publication tripwires."""

import os
from collections.abc import Callable
from pathlib import Path

import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.errors import AnalysisError
from marivo.analysis.materialization import terminal_store
from marivo.analysis.session.core import Session
from marivo.datasource.adapters import SourceSession
from marivo.semantic.reader import SemanticProject
from tests.analysis.graph.reference_fixtures import reference_data
from tests.analysis.graph.source_fixtures import author_source_project
from tests.analysis.materialization.domain_recovery_worker import snapshot
from tests.analysis.materialization.publication_fixtures import publication_counts
from tests.datasource.source_cases import source_case
from tests.shared_fixtures import catalog_value, run_ids
from tests.support.json import Json, encode
from tests.support.refs import as_ref


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
        session = mv.session.get_or_create("r94-refusal", timezone="UTC")
        entity = ms.ref.entity("sales.facts")
        measure = ms.ref.measure("sales.facts.amount")
        live = session.read(
            session.members(catalog_value(session.catalog, as_ref(entity).path, kind="entity")),
            {"value": catalog_value(session.catalog, as_ref(measure).path, kind="field")},
        )
        previous = live.persist()
        saved = snapshot(previous)
        foreign = mv.session.get_or_create("r94-foreign", timezone="UTC")
        other = foreign.read(
            foreign.members(catalog_value(foreign.catalog, as_ref(entity).path, kind="entity")),
            {"value": catalog_value(foreign.catalog, as_ref(measure).path, kind="field")},
        )
        published_before = publication_counts(session)
        calls: list[str] = []

        def tripwire(name: str) -> Callable[..., None]:
            def forbidden(*args: object, **kwargs: object) -> None:
                calls.append(name)
                raise AssertionError("Refusal attempted " + name)

            return forbidden

        for name in ("__enter__", "bind", "compile", "execute"):
            monkeypatch.setattr(SourceSession, name, tripwire("source." + name))
        monkeypatch.setattr(terminal_store, "admit", tripwire("terminal_store.admit"))
        refusals: dict[
            str, tuple[mv.AnalysisFrame, mv.AnalysisFrame | mv.Artifact, list[Session]]
        ] = {
            "artifact-input": (live, previous.artifact, [session]),
            "cross-session": (live, other, [session, foreign]),
        }
        reports: dict[str, list[Json]] = {}
        for refusal, (left, right, sessions) in refusals.items():
            before = [run_ids(item) for item in sessions]
            errors: list[Json] = []
            for operation in ("align", "compare"):
                with pytest.raises(AnalysisError) as caught:
                    if operation == "align":
                        left.align(right)  # type: ignore[arg-type]  # Exercise the runtime Artifact boundary.
                    else:
                        left.compare({"base": mv.baseline(right)})  # type: ignore[dict-item]  # Exercise the runtime Artifact boundary.
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
            if isinstance(right, mv.AnalysisFrame):
                with pytest.raises(AnalysisError):
                    right.compare(
                        {
                            "base": mv.baseline(
                                left, on=tuple(zip(right.axes, left.axes, strict=True))
                            )
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


def test_public_required_output_receipts_block_consumption_without_new_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    with source_case("duckdb", "table", tmp_path, monkeypatch, reference_data("duckdb")) as case:
        author_source_project("duckdb", case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("required-output", timezone="UTC")
        members = session.members(session.catalog.get("entity:sales.facts").value)
        metric = session.catalog.get("metric:sales.total").value
        current = session.observe(
            {"value": metric},
            by=mv.product(
                session.time_scope(start="2026-08-01", end="2026-08-03").by(mv.grain("day")),
                members,
            ),
        )
        baseline = session.observe(
            {"value": metric},
            by=mv.product(
                session.time_scope(start="2026-07-01", end="2026-07-03").by(mv.grain("day")),
                members,
            ),
        )
        previous = current.compare(
            {
                "base": mv.baseline(
                    baseline,
                    match=mv.ordinal(),
                    on=tuple(zip(current.axes, baseline.axes, strict=True)),
                )
            }
        ).persist()
        frame = previous.to_pandas()
        assert len(frame) == 6
        assert frame["value"].sum() == 6
        assert frame["base.baseline.value"].sum() == 0
        assert frame[["current.present", "base.present"]].all().all()
        data = previous.artifact._manifest.data
        assert any(item.name.startswith("calendar.") for item in data.attachments)
        saved = snapshot(previous, frame=frame)
        before = run_ids(session)
        published_before = publication_counts(session)
        calls: list[str] = []

        def forbidden(*args: object, **kwargs: object) -> None:
            calls.append("source-or-admission")
            raise AssertionError("Missing output receipt admitted work")

        for name in ("__enter__", "bind", "compile", "execute"):
            monkeypatch.setattr(SourceSession, name, forbidden)
        monkeypatch.setattr(terminal_store, "admit", forbidden)
        for rows in (data.rows, *(item.rows for item in data.attachments)):
            path = tmp_path / rows.receipt.project_relative_path
            offline = path.with_name(path.name + ".offline")
            path.rename(offline)
            try:
                reopened = session.persisted_frame(previous.artifact.id)
                for value in (previous, reopened):
                    with pytest.raises(AnalysisError) as caught:
                        value.to_pandas()
                    assert caught.value.expected and caught.value.received and caught.value.repair
                assert run_ids(session) == before and calls == []
                assert session._runtime.store.resources(session.id) == ()
            finally:
                offline.rename(path)
        assert snapshot(previous) == saved
        assert publication_counts(session) == published_before


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
        models += "event_time = ms.time_dimension_column(name='happened', entity=events, column='happened', parse=ms.timestamp(timezone='UTC'), is_default=True)\n"
        models += "amount_value = events_amount\ntime_value = event_time\n@ms.metric()\ndef total():\n    return amount_value.sum(time=time_value)\n"
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
        session = mv.session.get_or_create("r94-cross-datasource", timezone="UTC")
        members = session.members(session.catalog.get("entity:sales.subjects").value)
        previous = session.read(
            members,
            {
                "value": catalog_value(
                    session.catalog, "sales.subjects.subjects_amount", kind="field"
                )
            },
        ).persist()
        saved = snapshot(previous)
        before = run_ids(session)
        published_before = publication_counts(session)
        native_before = list(trace.native_sql)
        calls: list[str] = []

        def forbidden(*args: object, **kwargs: object) -> None:
            calls.append("business-read-or-admission")
            raise AssertionError("Cross-datasource graph admitted work")

        for name in ("compile", "execute"):
            monkeypatch.setattr(SourceSession, name, forbidden)
        monkeypatch.setattr(terminal_store, "admit", forbidden)
        with pytest.raises(AnalysisError) as caught:
            session.observe(
                {"value": session.catalog.get("metric:sales.total").value}, by=members
            ).persist()
        error = caught.value
        assert error.expected == "scalar relationship endpoints on one datasource"
        assert error.received == "sales.subjects"
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
