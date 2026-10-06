"""Independent local-file qualification and source admission boundaries."""

import os
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.errors import AnalysisError
from marivo.analysis.methods import builtin
from marivo.analysis.methods.physical import (
    NoTime,
    Qualified,
    ScalarType,
    SourceShape,
    TimeShape,
)
from marivo.analysis.methods.semantics import MethodKey
from marivo.datasource.adapters import SourceSession
from marivo.semantic.reader import SemanticProject
from tests.analysis.graph.reference_fixtures import reference_data
from tests.analysis.materialization.file_fixtures import author_file_case
from tests.datasource.source_cases import source_case
from tests.support.json import encode
from tests.support.source_trace import SourceTrace


def test_local_file_declarations_are_closed_to_verified_methods() -> None:
    for name in (
        "parts_transport",
        "metric.sum_zero",
        "state_rollup.sum_zero",
        "deviation.zscore",
        "deviation.read",
        "bind_project",
        "metric.mean",
        "row.sum",
    ):
        entries = [
            item
            for item in builtin.implementations(MethodKey(name))
            if isinstance(item.key.shape, SourceShape) and item.key.shape.form in ("csv", "json")
        ]
        assert len(entries) == {
            "parts_transport": 4,
            "metric.sum_zero": 2,
            "state_rollup.sum_zero": 4,
            "deviation.zscore": 2,
            "deviation.read": 2,
        }.get(name, 0)
        for item in entries:
            assert isinstance(item.key.shape, SourceShape)
            prior = name in ("parts_transport", "metric.sum_zero")
            input_type = (
                ScalarType("string")
                if prior
                else ScalarType("float64")
                if name == "deviation.read"
                else ScalarType("int64")
            )
            assert item.key.input_types == (input_type,)
            assert item.key.input_domains == ("entity",)
            assert item.key.route in (("ibis",) if prior else ("ibis", "ibis_python"))
            assert item.key.shape.backend == "duckdb"
            assert item.key.shape.time in (
                (NoTime(), TimeShape("instant", "us", "UTC"))
                if prior
                else (NoTime(),)
                if name.startswith("deviation.")
                else (TimeShape("instant", "us", "UTC"),)
            )
            assert isinstance(item.qualification, Qualified)
            # Persisted qualification identities are stable across test relocation.
            assert item.qualification.evidence_id == (
                "tests/test_r94_producer_recovery.py"
                if prior
                else "tests/test_r96_local_file_cost_routes.py"
            )


@pytest.mark.runtime
@pytest.mark.parametrize("profile", ("csv", "local-json"))
@pytest.mark.parametrize("case_kind", ("metadata", "float-sum", "integer-mean"))
def test_file_construction_and_refusals_never_submit_business_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    source_trace: SourceTrace,
    profile: str,
    case_kind: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    data = reference_data("duckdb")
    if case_kind == "float-sum":
        rows: list[dict[str, object]] = []
        for row in data.rows:
            amount = row["amount"]
            assert amount is None or isinstance(amount, int)
            rows.append({**row, "amount": float(amount) + 0.25 if amount is not None else None})
        data = replace(data, rows=rows)
    with source_case("duckdb", profile, tmp_path, monkeypatch, data) as case:
        author_file_case(case, data, profile, semantic_project_factory)
        if case_kind == "integer-mean":
            model = tmp_path / "models/semantic/sales/models.py"
            with model.open("a") as stream:
                stream.write("average=ms.aggregate(name='average', measure=amount, agg='mean')\n")
        session = mv.session.get_or_create("r94-file-admission", report_timezone="UTC")
        calls: list[str] = []

        def forbidden(*args: object, **kwargs: object) -> None:
            calls.append("business-read-or-admission")
            raise AssertionError("File construction/refusal attempted data work")

        monkeypatch.setattr(SourceSession, "compile", forbidden)
        monkeypatch.setattr(SourceSession, "batches", forbidden)
        monkeypatch.setattr(session._runtime.store, "admit", forbidden)
        members = session.members(ms.ref.entity("sales.facts"))
        received: str | None = None
        if case_kind == "metadata":
            members.observe(ms.ref.metric("sales.total"))
        else:
            with pytest.raises(AnalysisError) as caught:
                members.observe(
                    ms.ref.metric("sales.average" if case_kind == "integer-mean" else "sales.total")
                )
            error = caught.value
            assert error.expected == "an ordinary int64 sum-zero observation from local CSV/JSON"
            assert error.received and error.repair is not None and error.repair.action
            received = error.received
            assert ("float64" if case_kind == "float-sum" else "aggregation=mean") in received
        assert calls == [] and source_trace.native_sql == []
        assert session.runs().items == () and session._runtime.store.resources(session.id) == ()
        if directory := os.environ.get("MARIVO_R93_EVIDENCE_DIR"):
            Path(directory, f"file-admission-{profile}-{case_kind}.json").write_bytes(
                encode(
                    {
                        "profile": profile,
                        "case": case_kind,
                        "business_submissions": 0,
                        "runs": 0,
                        "resources": 0,
                        "received": received,
                        "boundary": "Local inferred R1 schema facts or exact unsupported observation; no business evaluation or new Run.",
                    }
                )
            )


@pytest.mark.runtime
@pytest.mark.parametrize("request_kind", ("http", "shadow-http", "parameterized"))
def test_json_request_sources_are_refused_before_source_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    request_kind: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    with source_case(
        "duckdb", "local-json", tmp_path, monkeypatch, reference_data("duckdb")
    ) as case:
        file = author_file_case(
            case, reference_data("duckdb"), "local-json", semantic_project_factory
        )
        model = tmp_path / "models/semantic/sales/models.py"
        original = f"md.json({str(file)!r})"
        if request_kind == "shadow-http":
            shadow = tmp_path / "http:/127.0.0.1:9/unfetched"
            shadow.parent.mkdir(parents=True)
            shadow.write_text("[]")
            assert Path("http://127.0.0.1:9/unfetched").is_file()
        replacement = (
            "md.json('http://127.0.0.1:9/unfetched')"
            if request_kind in ("http", "shadow-http")
            else f"md.json({str(file)!r}, query_params={{'page':md.source_param('page')}})"
        )
        assert original in model.read_text()
        model.write_text(model.read_text().replace(original, replacement))
        session = mv.session.get_or_create("r94-json-request-refusal", report_timezone="UTC")
        calls: list[str] = []

        def forbidden(*args: object, **kwargs: object) -> None:
            calls.append("source-open-or-admission")
            raise AssertionError("Unqualified JSON request opened a source")

        for name in ("__enter__", "bind", "compile", "batches"):
            monkeypatch.setattr(SourceSession, name, forbidden)
        monkeypatch.setattr(session._runtime.store, "admit", forbidden)
        with pytest.raises(AnalysisError) as caught:
            session.members(ms.ref.entity("sales.facts"))
        error = caught.value
        assert error.expected == "an existing local CSV or unparameterized GET JSON file on DuckDB"
        assert error.received and "JsonSourceIR" in error.received
        assert error.repair is not None and error.repair.action
        assert calls == [] and session.runs().items == ()
        assert session._runtime.store.resources(session.id) == ()


@pytest.mark.runtime
def test_local_csv_workflow_example_executes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    from tests.support.documentation import _blocks

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    data = reference_data("duckdb")
    with source_case("duckdb", "csv", tmp_path, monkeypatch, data) as case:
        file = author_file_case(case, data, "csv", semantic_project_factory)
        directory = tmp_path / "data"
        directory.mkdir()
        (directory / "facts.csv").write_bytes(file.read_bytes())
        declarations = next(
            block
            for block in _blocks("en", "analysis-workflow")
            if 'source=md.csv("data/facts.csv")' in block
        )
        (tmp_path / "models/semantic/sales/models.py").write_text(declarations)
        analysis = next(
            block
            for block in _blocks("en", "analysis-workflow")
            if 'get_or_create("file-analysis"' in block
        )
        namespace: dict[str, object] = {}
        exec(compile(analysis, "local-csv-workflow-example", "exec"), namespace)
        result = namespace["result"]
        assert isinstance(result, mv.MaterializedNumericRelation)
        frame = result.to_pandas().set_index(["member", "coord_0", "coord_1"])
        assert frame.value.to_dict() == {
            ("a", 9007199254740992, 1): 2,
            ("a", 9007199254740993, 2): 0,
            ("b", 9007199254740993, 1): 4,
        }
