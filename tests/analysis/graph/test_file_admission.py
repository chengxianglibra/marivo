"""Independent local-file qualification and source admission boundaries."""

import os
from collections.abc import Callable
from dataclasses import replace
from importlib.util import cache_from_source
from pathlib import Path

import pytest

import marivo.analysis as mv
from marivo._help.render import render_help_text
from marivo.analysis.errors import AnalysisError
from marivo.datasource.adapters import SourceSession
from marivo.semantic.reader import SemanticProject
from tests.analysis.graph.reference_fixtures import reference_data
from tests.analysis.materialization.file_fixtures import author_file_case
from tests.datasource.source_cases import source_case
from tests.shared_fixtures import catalog_value, terminal_runs
from tests.support.json import encode
from tests.support.source_trace import SourceTrace


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
                stream.write(
                    "@ms.metric()\ndef average():\n    return amount_value.mean(time=happened_value)\n"
                )
        session = mv.session.get_or_create("r94-file-admission", timezone="UTC")
        calls: list[str] = []

        def forbidden(*args: object, **kwargs: object) -> None:
            calls.append("business-read-or-admission")
            raise AssertionError("File construction/refusal attempted data work")

        with monkeypatch.context() as patch:
            patch.setattr(SourceSession, "compile", forbidden)
            patch.setattr(SourceSession, "execute", forbidden)
            metric = catalog_value(
                session.catalog,
                "sales.average" if case_kind == "integer-mean" else "sales.total",
                kind="metric",
            )
            query = session.observe({"value": metric})
            query.show()
            assert calls == [] and source_trace.native_sql == []
            assert terminal_runs(session) == ()
            assert session._runtime.store.resources(session.id) == ()
        result = query.persist().to_pandas()["value"].iloc[0]
        assert result == {"metadata": 6, "float-sum": 6.5, "integer-mean": 3.0}[case_kind]
        assert session._runtime.store.resources(session.id) == ()
        received = None
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
                        "boundary": "Construction is source-free; execution evaluates the admitted local numeric Metric.",
                    }
                )
            )


@pytest.mark.parametrize("request_kind", ("http", "post", "parameterized"))
def test_unresolved_json_parameters_are_refused_before_source_open(
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
        replacement = (
            "md.json('http://127.0.0.1:9/unfetched', query_params={'page':md.source_param('page')})"
            if request_kind == "http"
            else "md.json('http://127.0.0.1:9/unfetched', method='POST', body={'page':md.source_param('page')})"
            if request_kind == "post"
            else f"md.json({str(file)!r}, query_params={{'page':md.source_param('page')}})"
        )
        assert original in model.read_text()
        model.write_text(model.read_text().replace(original, replacement))
        # The generated file can retain both its byte length and integer mtime.
        # Remove its initial import's pyc so this case tests the edited request.
        Path(cache_from_source(str(model))).unlink(missing_ok=True)
        session = mv.session.get_or_create("r94-json-request-refusal", timezone="UTC")
        calls: list[str] = []

        def forbidden(*args: object, **kwargs: object) -> None:
            calls.append("source-open-or-admission")
            raise AssertionError("Unqualified JSON request opened a source")

        for name in ("__enter__", "bind", "compile", "execute"):
            monkeypatch.setattr(SourceSession, name, forbidden)
        query = session.observe({"value": session.catalog.get("metric:sales.total")})
        with pytest.raises(AnalysisError) as caught:
            query.persist()
        error = caught.value
        assert calls == []
        assert error.expected and "concrete values" in error.expected
        assert (
            error.received and "missing_parameters" in error.received and "page" in error.received
        )
        assert error.repair is not None and error.repair.action
        target = error.repair.help_target.display
        assert target == "analysis.analysis_frame.compute"
        assert f"marivo.help('{target}')" in str(error)
        assert "Target: analysis.analysis_frame.compute" in render_help_text(target)[0]
        assert calls == [] and terminal_runs(session) == ()
        assert session._runtime.store.resources(session.id) == ()
