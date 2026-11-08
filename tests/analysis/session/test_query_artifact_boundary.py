"""R2.1 captures definitions and terminal data without pretending to execute."""

from __future__ import annotations

import sqlite3
import textwrap
from collections.abc import Callable
from dataclasses import fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import pandas as pd
import pytest

import marivo
import marivo.analysis as mv
from marivo._definition.expressions import MetricNode
from marivo._definition.relations import ObserveQuery
from marivo._definition.types import Owner
from marivo.analysis.core.artifact_data import (
    ArtifactData,
    ArtifactManifest,
    DataAxis,
    DataColumn,
    DataSchema,
    DecimalDataType,
    ParquetHandle,
    ScalarDataType,
    TimestampDataType,
)
from marivo.analysis.core.frame_data import (
    CompletedFrameData,
    CompletedFrameResult,
    ExpressionFrameBacking,
    LiteralFrameBinding,
    SourceFrameBinding,
)
from marivo.analysis.core.frame_metadata import EvaluationRecord, FrameResultMetadata
from marivo.analysis.core.graph import BoundGraph
from marivo.analysis.core.lineage import empty as empty_lineage
from marivo.analysis.core.model import BoundQuantity
from marivo.analysis.errors import AnalysisError, SessionNotFoundError
from marivo.analysis.materialization.contracts import FileEntry, LocalReceipt
from marivo.analysis.materialization.errors import IntegrityError
from marivo.analysis.materialization.layout import MaterializationLayout
from marivo.analysis.materialization.terminal_store import describe
from marivo.analysis.session.core import Session
from marivo.semantic._deferred_semantics import consume_metric
from marivo.semantic._expression_values import MetricExpression
from marivo.semantic.reader import SemanticProject
from tests.shared_fixtures import (
    catalog_get,
    numeric_literal_node,
    numeric_program_frame,
    terminal_runs,
)


def _metric(value: int) -> MetricNode:
    return numeric_literal_node(value)


def _manifest() -> ArtifactManifest:
    schema = DataSchema(
        (),
        (DataColumn("v0", "revenue", ScalarDataType("int64"), True, "CNY"),),
        value_paths=(("revenue",),),
    )
    receipt = LocalReceipt("saved/primary", (FileEntry("part.parquet", 42),), 1, 42)
    return ArtifactManifest(
        "store_exact",
        "artifact_exact",
        "session_exact",
        "run_exact",
        ArtifactData(schema, ParquetHandle(receipt)),
        describe(
            FrameResultMetadata(
                "result_exact",
                lineage=empty_lineage(schema),
                evaluation=EvaluationRecord(
                    datetime(2026, 1, 1, tzinfo=timezone.utc), (), ("literal",)
                ),
            )
        ),
    )


def _graph(frame: mv.AnalysisFrame) -> BoundGraph:
    assert isinstance(frame._backing, ExpressionFrameBacking)
    assert isinstance(frame._backing.root, (LiteralFrameBinding, SourceFrameBinding))
    return frame._backing.root.graph


def test_named_metrics_capture_without_catalog_schema_rows_or_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create("capture", timezone="UTC")

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("construction tried to open semantic sources")

    monkeypatch.setattr(Session, "_sources", forbidden)
    mapping = {"revenue": _metric(10), "aov": _metric(5)}
    query = numeric_program_frame(session, mapping)
    mapping["later"] = _metric(99)
    assert type(query) is mv.AnalysisFrame
    assert tuple(q.name for q in _graph(query).signature.quantities) == ("revenue", "aov")
    graph = _graph(query)
    assert isinstance(graph.root, ObserveQuery)
    assert graph.root.metrics == (
        ("revenue", numeric_literal_node(10)),
        ("aov", numeric_literal_node(5)),
    )
    assert _graph(query).signature.owner == Owner(session.id, session._runtime.store.store_id)
    query.show()
    rendered = capsys.readouterr().out
    assert terminal_runs(session) == ()
    assert query.compute().to_pandas()["revenue"].tolist() == [10]
    assert terminal_runs(session) == ()
    artifacts = [query.persist() for _ in range(2)]
    assert len({artifact.artifact_id for artifact in artifacts}) == 2
    assert len(terminal_runs(session)) == 2
    assert all(run.lifecycle == "succeeded" for run in terminal_runs(session))
    assert session._sources_value is None and session._catalog_value is None


@pytest.mark.parametrize("composition", ("direct", "scale", "reverse", "ratio", "negate"))
def test_catalog_metrics_capture_the_r1_definition_closure(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    composition: str,
) -> None:
    import marivo.datasource.backends as backends

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Deferred capture opened a datasource")

    monkeypatch.setattr(backends, "_build_backend_from_effective", forbidden)
    semantic_project_factory(
        {
            "sales/_domain.py": 'import marivo.semantic as ms\nms.domain(name="sales", owner="Analytics", default=True)\n',
            "sales/metrics.py": textwrap.dedent("""                import marivo.datasource as md
                import marivo.semantic as ms
                orders_ref = ms.entity(name="orders", datasource=ms.ref.datasource("warehouse"), source=md.table("orders"))
                amount_ref = ms.measure_column(name="amount", entity=orders_ref, column="amount", additivity=ms.additive_all(), unit="CNY")
                amount = amount_ref

                @ms.metric()
                def revenue():
                    return amount.sum()

                @ms.metric()
                def double_revenue():
                    return revenue + revenue
            """),
        },
        load=False,
    )
    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create("catalog-capture")
    revenue = catalog_get(session.catalog, "metric:sales.revenue", kind="metric")
    double = catalog_get(session.catalog, "metric:sales.double_revenue", kind="metric")
    variants: dict[str, dict[str, MetricExpression[Literal["numeric"]]]] = {
        "direct": {"revenue": revenue, "double": double},
        "scale": {"result": revenue * 2},
        "reverse": {"result": 2 * revenue},
        "ratio": {"result": (double - revenue) / revenue},
        "negate": {"result": -revenue},
    }
    outputs = variants[composition]
    query = session.observe(outputs)
    assert tuple(
        (q.name, q.definition)
        for q in _graph(query).signature.quantities
        if isinstance(q, BoundQuantity)
    ) == tuple((name, consume_metric(metric).node) for name, metric in outputs.items())
    if composition != "direct":
        assert len(_graph(query).signature.quantities) == 1
        assert dict(_graph(query).signature.declared_metrics) == dict(
            (*consume_metric(revenue)._declared, *consume_metric(double)._declared)
            if composition == "ratio"
            else consume_metric(revenue)._declared
        )
    assert _graph(query).signature.declared_metrics
    assert _graph(query).fingerprint
    assert terminal_runs(session) == ()


def test_saved_frame_owns_identity_and_rejects_invalid_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create("saved-identity")
    pending = session.values(by=(), columns={"value": None}, rows=(((), {"value": 7}),))
    saved = pending.persist()
    assert pending.session_id == saved.session_id == session.id
    assert saved.artifact_id.startswith("artifact_")


def test_publication_tables_and_committed_data_have_distinct_contracts() -> None:
    manifest = _manifest()
    frame = pd.DataFrame({"v0": pd.Series([10], dtype="Int64")})
    publication = CompletedFrameResult(
        CompletedFrameData(manifest.data.schema, frame),
        FrameResultMetadata(
            manifest.metadata.result_id,
            evaluation=EvaluationRecord(manifest.metadata.evaluated_at, (), ("literal",)),
            lineage=manifest.metadata.lineage,
        ),
    )
    pd.testing.assert_frame_equal(publication.data.copy_rows().astype("Int64"), frame)
    assert isinstance(manifest.data.rows, ParquetHandle)
    forbidden = {
        "signature",
        "query",
        "definition",
        "components",
        "continuation",
        "fingerprint",
    }
    for value in (manifest, manifest.data, manifest.data.schema, publication):
        assert not forbidden.intersection(field.name for field in fields(value))


def test_data_schema_preserves_composite_keys_types_and_output_order() -> None:
    keys = (
        DataColumn("k0", "tenant", ScalarDataType("string"), False),
        DataColumn("k1", "id", ScalarDataType("int64"), False),
    )
    money = DataColumn("v0", "money", DecimalDataType(28, 6), True, "CNY")
    time = DataColumn("v1", "time", TimestampDataType("ns", "Asia/Shanghai"), True)
    schema = DataSchema(
        (DataAxis("customer", keys),),
        (money, time),
        value_paths=(("money",), ("time",)),
    )
    assert schema.key_fields == ("k0", "k1")
    assert schema.columns == (*keys, money, time)
    assert DataSchema((), ()).key_fields == ()


def test_session_reopen_remains_same_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    first = mv.session.get_or_create("same-generation", timezone="UTC")
    resumed = mv.session.resume(first.id)
    named = mv.session.resume(first.id)
    assert resumed.id == named.id == first.id
    assert MaterializationLayout(tmp_path).generation == 28
    assert terminal_runs(resumed) == ()


def test_old_graph_metadata_is_rejected_without_decoding_or_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create("old-output")
    with session._runtime.store._write() as connection:
        connection.execute(
            "INSERT INTO dataset_artifacts VALUES (?,?,?,?,?)",
            (
                "artifact_old",
                session.id,
                "old-key",
                "not even a decodable graph",
                "2026-10-16T00:00:00Z",
            ),
        )
    with pytest.raises(IntegrityError) as caught:
        session.persisted_frame("artifact_old")
    assert caught.value.stage in {"publication", "terminal_output"}
    with sqlite3.connect(session._runtime.store.db_path) as connection:
        assert connection.execute(
            "SELECT descriptor_payload FROM dataset_artifacts"
        ).fetchone() == ("not even a decodable graph",)


def test_old_session_is_not_found_without_rewriting_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    path = tmp_path / ".marivo/analysis/generations/v11/session_store.db"
    path.parent.mkdir(parents=True)
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version=11")
    with pytest.raises(SessionNotFoundError) as caught:
        mv.session.resume("old-session")
    assert caught.value.repair is not None
    assert "v11" in caught.value.repair.action


@pytest.mark.parametrize("columns", (None, False, 0, {}))
def test_zero_axis_values_reject_malformed_mapping_before_catalog_or_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, columns: object
) -> None:
    from marivo.datasource.adapters import SourceSession

    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create("invalid-columns")

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("malformed mapping attempted source configuration or I/O")

    monkeypatch.setattr(Session, "_sources", forbidden)
    for name in ("__enter__", "bind", "compile", "execute"):
        monkeypatch.setattr(SourceSession, name, forbidden)
    with pytest.raises(AnalysisError) as failure:
        session.values(by=(), columns=columns, rows=(((), {"value": 1}),))  # type: ignore[arg-type]  # Deliberately malformed public mapping.
    error = failure.value
    assert error.expected == "named columns and finite typed literal rows"
    assert error.received and error.repair is not None and error.repair.action
    assert "literal row" in error.repair.action


def test_new_help_targets_resolve_and_disclose_phase_boundary(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from marivo._help.render import help as public_help

    assert marivo.help is public_help
    for target in (
        "analysis.session.observe",
        "analysis.analysis_frame",
        "analysis.analysis_frame.show",
        "analysis.analysis_frame.compute",
        "analysis.analysis_frame.persist",
        "analysis.session.frame",
        "analysis.session.persisted_frame",
        "analysis.session.population_from",
        "analysis.frame_axis",
        "analysis.analysis_frame.artifact_id",
        "analysis.analysis_frame.session_id",
        "analysis.analysis_frame.source_reads",
    ):
        public_help(target)
    public_help(mv.AnalysisFrame)
    rendered = capsys.readouterr().out


def test_lifecycle_help_discloses_actual_effects_for_string_and_callable(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from marivo._help.render import help as public_help

    assert marivo.help is public_help
    for target in ("analysis.analysis_frame.compute", mv.AnalysisFrame.compute):
        public_help(target)
        text = capsys.readouterr().out
    for target in ("analysis.analysis_frame.persist", mv.AnalysisFrame.persist):
        public_help(target)
        text = capsys.readouterr().out


@pytest.mark.parametrize("name", ("show",))
def test_artifact_read_help_discloses_committed_reading_for_string_and_callable(
    capsys: pytest.CaptureFixture[str], name: str
) -> None:
    from marivo._help.render import help as public_help

    assert marivo.help is public_help
    for target in (f"analysis.analysis_frame.{name}", getattr(mv.AnalysisFrame, name)):
        public_help(target)
        text = capsys.readouterr().out
