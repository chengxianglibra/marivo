"""R2.1 captures definitions and terminal data without pretending to execute."""

from __future__ import annotations

import sqlite3
import textwrap
from collections.abc import Callable
from dataclasses import FrozenInstanceError, fields, replace
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

import marivo
import marivo.analysis as mv
from marivo._definition.expressions import MetricNode
from marivo._definition.relations import ObserveQuery, PopulationProgram
from marivo._definition.types import Owner
from marivo.analysis.core.artifact_data import (
    ArtifactData,
    ArtifactManifest,
    DataAxis,
    DataColumn,
    DataSchema,
    DecimalDataType,
    OutputCoordinate,
    OutputEvidence,
    ParquetHandle,
    ScalarDataType,
    TimestampDataType,
)
from marivo.analysis.core.frame_audit import EvaluationRecord, FrameAudit, FrameResultMetadata
from marivo.analysis.core.frame_data import (
    CompletedFrameData,
    CompletedFrameResult,
    ExpressionFrameBacking,
    LiteralFrameBinding,
    SourceFrameBinding,
)
from marivo.analysis.core.graph import BoundGraph
from marivo.analysis.core.model import BoundQuantity, CoreRuleError
from marivo.analysis.dsl.artifact import Artifact, ArtifactReader
from marivo.analysis.errors import AnalysisError, ArtifactNotFoundError
from marivo.analysis.materialization.contracts import FileEntry, LocalReceipt
from marivo.analysis.materialization.errors import IntegrityError
from marivo.analysis.materialization.layout import MaterializationLayout
from marivo.analysis.refs import ArtifactRef
from marivo.analysis.session.core import Session
from marivo.semantic._deferred_semantics import consume_metric
from marivo.semantic.reader import SemanticProject
from tests.shared_fixtures import numeric_literal_node, numeric_program_frame


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
        FrameResultMetadata(
            "result_exact",
            FrameAudit(
                (), EvaluationRecord(datetime(2026, 1, 1, tzinfo=timezone.utc), (), ("literal",))
            ),
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
    session = mv.session.create("capture", timezone="UTC")

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
    assert "AnalysisFrame" in repr(query) and "\n" not in repr(query)
    query.show()
    rendered = capsys.readouterr().out
    assert "revenue" in rendered and "aov" in rendered and "pending" in rendered
    assert session.runs().items == ()
    assert query.compute().to_pandas()[("value", "revenue")].tolist() == [10]
    assert session.runs().items == ()
    artifacts = [query.persist() for _ in range(2)]
    assert len({artifact.artifact.id for artifact in artifacts}) == 2
    assert len(session.runs().items) == 2
    assert all(run.lifecycle == "succeeded" for run in session.runs().items)
    assert session._sources_value is None and session._catalog_value is None
    with pytest.raises(FrozenInstanceError):
        query._backing = query._backing  # type: ignore[misc]  # Verify frozen backing at runtime.


def test_definition_ownership_is_checked_without_source_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    session = mv.session.create("receiver")
    foreign = mv.session.create("other")
    import marivo.semantic as ms
    from marivo._definition.relations import SourceIdentities
    from marivo._definition.types import AxisBinding, ExactRef
    from marivo.analysis.dsl.population import Population

    entity = ExactRef(ms.ref.entity("sales.customers"), "v1")
    domain = Population(
        PopulationProgram(
            SourceIdentities(entity),
            Owner(foreign.id, foreign._runtime.store.store_id),
            (AxisBinding(entity, None, (), "identity"),),
        )
    )
    with pytest.raises(CoreRuleError, match="Session"):
        session.frame(domain)
    assert session.runs().items == ()


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
            "sales/metrics.py": textwrap.dedent("""\
                import marivo.datasource as md
                import marivo.semantic as ms
                orders_ref = ms.entity(name="orders", datasource=ms.ref.datasource("warehouse"), source=md.table("orders"))
                amount_ref = ms.measure_column(name="amount", entity=orders_ref, column="amount", additivity=ms.additive_all(), unit="CNY")
                finalized = ms.finalize()
                amount = finalized.resolve(amount_ref)

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
    session = mv.session.create("catalog-capture")
    revenue = session.catalog.metrics.get("sales.revenue")
    double = session.catalog.metrics.get("sales.double_revenue")
    outputs = {
        "direct": {"revenue": revenue, "double": double},
        "scale": {"result": revenue * 2},
        "reverse": {"result": 2 * revenue},
        "ratio": {"result": (double - revenue) / revenue},
        "negate": {"result": -revenue},
    }[composition]
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
    assert session.runs().items == ()


@pytest.mark.parametrize("bad", ("artifact_exact", 1, None))
def test_observe_does_not_resolve_ids_or_other_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad: object
) -> None:
    monkeypatch.chdir(tmp_path)
    session = mv.session.create("reject-input")
    with pytest.raises(CoreRuleError, match="Metric expression"):
        session.observe({"bad": bad})  # type: ignore[dict-item]  # Deliberately invalid Metric input.
    assert session.runs().items == ()


def test_terminal_artifact_has_only_identity_and_reader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from unittest.mock import create_autospec

    reader = create_autospec(ArtifactReader, instance=True)
    artifact = Artifact._from_manifest(_manifest(), reader)
    assert artifact.id == "artifact_exact" and artifact.session_id == "session_exact"
    assert "Artifact id=artifact_exact" in repr(artifact) and "\n" not in repr(artifact)
    assert tuple(f.name for f in fields(artifact)) == ("_manifest", "_reader")
    assert not reader.mock_calls
    for name in (
        "execute",
        "contract",
        "select",
        "observe",
        "population",
        "where",
        "rollup",
        "compare",
    ):
        assert not hasattr(artifact, name)
    with pytest.raises(FrozenInstanceError):
        artifact._manifest = artifact._manifest  # type: ignore[misc]  # Verify frozen audit handle at runtime.
    with pytest.raises(TypeError):
        Artifact()
    monkeypatch.chdir(tmp_path)
    session = mv.session.create("reject-artifact")
    with pytest.raises(CoreRuleError, match="Metric expression"):
        session.observe({"bad": artifact})  # type: ignore[dict-item]  # Audit output cannot be a Metric.
    with pytest.raises(CoreRuleError, match="Metric expression"):
        session.observe({"bad": object()})  # type: ignore[dict-item]  # Arbitrary objects cannot be Metrics.
    assert not hasattr(mv, "Result")
    with pytest.raises(CoreRuleError, match="ID string"):
        session.persisted_frame(artifact)  # type: ignore[arg-type]  # This entry accepts only exact ID strings.
    with pytest.raises(CoreRuleError, match="ID string"):
        session.persisted_frame(ArtifactRef(ref="artifact_exact"))  # type: ignore[arg-type]  # Typed historical refs are not ID strings.
    assert not reader.mock_calls


def test_publication_tables_and_committed_data_have_distinct_contracts() -> None:
    manifest = _manifest()
    frame = pd.DataFrame({"v0": pd.Series([10], dtype="Int64")})
    publication = CompletedFrameResult(
        CompletedFrameData(manifest.data.schema, frame), manifest.metadata
    )
    pd.testing.assert_frame_equal(publication.data.copy_rows().astype("Int64"), frame)
    assert isinstance(manifest.data.rows, ParquetHandle)
    assert not hasattr(manifest.data, "findings")
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
    with pytest.raises(CoreRuleError):
        replace(manifest.data, rows=frame)  # type: ignore[arg-type]  # Saved data requires a receipt.
    with pytest.raises(CoreRuleError):
        CompletedFrameData(manifest.data.schema, manifest.data.rows)  # type: ignore[arg-type]  # Completed data requires actual rows.
    with pytest.raises(CoreRuleError, match="output scalar"):
        OutputCoordinate("k0", _metric(1))  # type: ignore[arg-type]  # Inert audit rejects executable definitions.
    with pytest.raises(CoreRuleError, match="output scalar"):
        OutputEvidence("check", "invalid semantic payload", (("value", _metric(1)),))  # type: ignore[arg-type]  # Inert audit rejects executable definitions.


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
    with pytest.raises(CoreRuleError):
        DataSchema(schema.axes, (money, money), value_paths=(("money",), ("money",)))
    with pytest.raises(CoreRuleError):
        DataSchema((), (), (money,), presence_paths=(("money",),))


def test_session_reopen_remains_same_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    first = mv.session.create("same-generation", timezone="UTC")
    resumed = mv.session.resume(first.id)
    named = mv.session.resume(first.id)
    assert resumed.id == named.id == first.id
    assert MaterializationLayout(tmp_path).generation == 13
    assert resumed.runs().items == ()


def test_old_graph_metadata_is_rejected_without_decoding_or_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    session = mv.session.create("old-output")
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
    before = session._runtime.store.db_path.read_bytes()
    with pytest.raises(IntegrityError) as caught:
        session.persisted_frame("artifact_old")
    assert caught.value.stage in {"publication", "terminal_output"}
    assert session._runtime.store.db_path.read_bytes() == before
    with pytest.raises(ArtifactNotFoundError):
        session.persisted_frame("missing")
    with sqlite3.connect(session._runtime.store.db_path) as connection:
        assert connection.execute(
            "SELECT descriptor_payload FROM dataset_artifacts"
        ).fetchone() == ("not even a decodable graph",)


def test_old_store_is_rejected_without_rewriting_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    path = tmp_path / ".marivo/analysis/generations/v11/session_store.db"
    path.parent.mkdir(parents=True)
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version=11")
    before = path.read_bytes()
    files = tuple(path.parent.iterdir())
    with pytest.raises(IntegrityError) as caught:
        mv.session.resume("old-session")
    assert caught.value.stage == "store_generation"
    assert path.read_bytes() == before and tuple(path.parent.iterdir()) == files
    assert not (path.parent.parent / "v12").exists()


@pytest.mark.parametrize("columns", (None, False, 0, {}))
def test_zero_axis_values_reject_malformed_mapping_before_catalog_or_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, columns: object
) -> None:
    from marivo.datasource.adapters import SourceSession

    monkeypatch.chdir(tmp_path)
    session = mv.session.create("invalid-columns")

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
    assert error.run_ref is None
    assert session._catalog_value is None and session._sources_value is None
    assert session.runs().items == ()
    assert session._runtime.store.resources(session.id) == ()


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
        "analysis.artifact",
        "analysis.artifact.show",
        "analysis.artifact.findings",
        "analysis.artifact.evidence_digest",
    ):
        public_help(target)
        assert target in capsys.readouterr().out
    public_help(mv.Artifact)
    rendered = capsys.readouterr().out
    assert "audit" in rendered.lower() and "AnalysisFrame" in rendered
    assert "committed" in rendered


def test_lifecycle_help_discloses_actual_effects_for_string_and_callable(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from marivo._help.render import help as public_help

    assert marivo.help is public_help
    for target in ("analysis.analysis_frame.compute", mv.AnalysisFrame.compute):
        public_help(target)
        text = capsys.readouterr().out
        assert "No Run or Artifact is created" in text
        assert "memory" in text and "stored" in text
    for target in ("analysis.analysis_frame.persist", mv.AnalysisFrame.persist):
        public_help(target)
        text = capsys.readouterr().out
        assert "publication admission" in text and "without recomputing" in text
        assert "stored" in text and "returns itself" in text


@pytest.mark.parametrize("name", ("show", "findings", "evidence_digest"))
def test_artifact_read_help_discloses_committed_reading_for_string_and_callable(
    capsys: pytest.CaptureFixture[str], name: str
) -> None:
    from marivo._help.render import help as public_help

    assert marivo.help is public_help
    for target in (f"analysis.artifact.{name}", getattr(mv.Artifact, name)):
        public_help(target)
        text = capsys.readouterr().out
        assert "submission" in text or "committed" in text or "saved" in text
        assert "unavailable until" not in text


@pytest.mark.parametrize(
    ("value", "target", "fields"),
    (
        (
            OutputEvidence("check", "Output checked.", (("rows", 1),)),
            "runtime.output_evidence",
            ("kind:", "summary:", "values:", "coordinates:"),
        ),
        (
            OutputCoordinate("key.0", 1),
            "runtime.output_coordinate",
            ("slot_id:", "value:"),
        ),
    ),
)
def test_terminal_record_help_resolves_nested_types_and_methods(
    value: OutputEvidence | OutputCoordinate,
    target: str,
    fields: tuple[str, ...],
    capsys: pytest.CaptureFixture[str],
) -> None:
    from marivo._help.render import help as public_help

    assert marivo.help is public_help
    rendered = []
    for form in (f"analysis.{target}", type(value)):
        public_help(form)
        text = capsys.readouterr().out
        assert f"analysis.{target}" in text
        assert all(field in text for field in fields)
        rendered.append(text)
    assert rendered[0] == rendered[1]
    for method in (value.show, value.render):
        public_help(method)
        assert "analysis.runtime.values." in capsys.readouterr().out
    assert type(value).__name__ not in mv.__all__
