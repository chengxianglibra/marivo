"""R2.1 captures definitions and terminal data without pretending to execute."""

from __future__ import annotations

import sqlite3
import textwrap
from dataclasses import FrozenInstanceError, fields, replace
from pathlib import Path

import pandas as pd
import pytest

import marivo
import marivo.analysis as mv
from marivo._definition.expressions import MetricNode
from marivo._definition.relations import PopulationProgram
from marivo._definition.types import Owner
from marivo.analysis.core.artifact_data import (
    ArtifactData,
    ArtifactManifest,
    ArtifactPublication,
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
from marivo.analysis.core.model import CoreRuleError
from marivo.analysis.dsl.artifact import Artifact, ArtifactReader
from marivo.analysis.errors import ArtifactNotFoundError
from marivo.analysis.materialization.contracts import FileEntry, LocalReceipt
from marivo.analysis.materialization.errors import IntegrityError
from marivo.analysis.materialization.layout import MaterializationLayout
from marivo.analysis.refs import ArtifactRef
from marivo.analysis.session.core import Session
from marivo.semantic._deferred_semantics import consume_metric
from tests.shared_fixtures import numeric_literal_node, numeric_program_query


def _metric(value: int) -> MetricNode:
    return numeric_literal_node(value)


def _manifest() -> ArtifactManifest:
    schema = DataSchema((), (DataColumn("v0", "revenue", ScalarDataType("int64"), True, "CNY"),))
    receipt = LocalReceipt("saved/primary", (FileEntry("part.parquet", 42),), 1, 42)
    return ArtifactManifest(
        "artifact_exact", "session_exact", "run_exact", ArtifactData(schema, ParquetHandle(receipt))
    )


def test_named_metrics_capture_without_catalog_schema_rows_or_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create("capture", report_timezone="UTC")

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("construction tried to open semantic sources")

    monkeypatch.setattr(Session, "_sources", forbidden)
    mapping = {"revenue": _metric(10), "aov": _metric(5)}
    query = numeric_program_query(session, mapping)
    mapping["later"] = _metric(99)
    assert type(query) is mv.Query
    assert tuple(q.name for q in query._graph.signature.quantities) == ("revenue", "aov")
    assert query._graph.root.metrics == (
        ("revenue", numeric_literal_node(10)),
        ("aov", numeric_literal_node(5)),
    )
    assert query._graph.signature.owner == Owner(session.id, session._runtime.store.store_id)
    assert "outputs=2" in repr(query) and "\n" not in repr(query)
    query.contract().show()
    rendered = capsys.readouterr().out
    assert "revenue" in rendered and "aov" in rendered and "unresolved" in rendered
    assert session.runs().items == ()
    artifacts = [query.execute() for _ in range(2)]
    assert len({artifact.id for artifact in artifacts}) == 2
    assert len(session.runs().items) == 2
    assert all(run.lifecycle == "succeeded" for run in session.runs().items)
    assert session._sources_value is None and session._catalog_value is None
    with pytest.raises(FrozenInstanceError):
        query._graph = query._graph
    with pytest.raises(CoreRuleError):
        bool(query)


def test_definition_ownership_is_checked_without_source_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create("receiver")
    foreign = mv.session.get_or_create("other")
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
        session.query(domain)
    assert session.runs().items == ()


@pytest.mark.parametrize("composition", ("direct", "scale", "reverse", "ratio", "negate"))
def test_catalog_metrics_capture_the_r1_definition_closure(
    semantic_project_factory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, composition: str
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
    session = mv.session.get_or_create("catalog-capture")
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
    assert tuple((q.name, q.definition) for q in query._graph.signature.quantities) == tuple(
        (name, consume_metric(metric).node) for name, metric in outputs.items()
    )
    if composition != "direct":
        assert len(query._graph.signature.quantities) == 1
        assert dict(query._graph.signature.declared_metrics) == dict(
            (*consume_metric(revenue)._declared, *consume_metric(double)._declared)
            if composition == "ratio"
            else consume_metric(revenue)._declared
        )
    assert query._graph.signature.declared_metrics
    assert query._graph.fingerprint
    assert session.runs().items == ()


@pytest.mark.parametrize("bad", ("artifact_exact", 1, None))
def test_observe_does_not_resolve_ids_or_other_values(tmp_path, monkeypatch, bad) -> None:
    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create("reject-input")
    with pytest.raises(CoreRuleError, match="Metric expression"):
        session.observe({"bad": bad})
    assert session.runs().items == ()


def test_terminal_artifact_has_only_identity_and_reader(tmp_path, monkeypatch) -> None:
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
        artifact._manifest = artifact._manifest
    with pytest.raises(TypeError):
        Artifact()
    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create("reject-artifact")
    with pytest.raises(CoreRuleError, match="Metric expression"):
        session.observe({"bad": artifact})
    with pytest.raises(CoreRuleError, match="Metric expression"):
        session.observe({"bad": object()})
    assert not hasattr(mv, "Result")
    with pytest.raises(CoreRuleError, match="ID string"):
        session.artifact(artifact)
    with pytest.raises(CoreRuleError, match="ID string"):
        session.artifact(ArtifactRef("artifact_exact"))
    assert not reader.mock_calls


def test_publication_tables_and_committed_data_have_distinct_contracts() -> None:
    manifest = _manifest()
    frame = pd.DataFrame({"v0": pd.Series([10], dtype="Int64")})
    publication = ArtifactPublication(
        manifest.artifact_id,
        manifest.session_id,
        manifest.run_id,
        manifest.data.schema,
        frame,
        evidence=(OutputEvidence("read", "one output row read"),),
    )
    assert publication.rows is frame
    assert isinstance(manifest.data.rows, ParquetHandle)
    assert not hasattr(manifest.data, "findings")
    forbidden = {
        "signature",
        "graph",
        "query",
        "definition",
        "components",
        "continuation",
        "fingerprint",
    }
    for value in (manifest, manifest.data, manifest.data.schema, publication):
        assert not forbidden.intersection(field.name for field in fields(value))
    with pytest.raises(CoreRuleError):
        replace(manifest.data, rows=frame)
    with pytest.raises(CoreRuleError):
        replace(publication, rows=manifest.data.rows)
    with pytest.raises(CoreRuleError, match="output scalar"):
        OutputCoordinate("k0", _metric(1))
    with pytest.raises(CoreRuleError, match="output scalar"):
        OutputEvidence("check", "invalid semantic payload", (("value", _metric(1)),))


def test_data_schema_preserves_composite_keys_types_and_output_order() -> None:
    keys = (
        DataColumn("k0", "tenant", ScalarDataType("string"), False),
        DataColumn("k1", "id", ScalarDataType("int64"), False),
    )
    money = DataColumn("v0", "money", DecimalDataType(28, 6), True, "CNY")
    time = DataColumn("v1", "time", TimestampDataType("ns", "Asia/Shanghai"), True)
    schema = DataSchema((DataAxis("customer", keys),), (money, time))
    assert schema.key_fields == ("k0", "k1")
    assert schema.columns == (*keys, money, time)
    assert DataSchema((), ()).key_fields == ()
    with pytest.raises(CoreRuleError):
        DataSchema(schema.axes, (money, money))
    with pytest.raises(CoreRuleError):
        DataSchema((), (), (money,))


def test_session_reopen_remains_same_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    first = mv.session.get_or_create("same-generation", report_timezone="UTC")
    resumed = mv.session.resume(first.id, by="id")
    named = mv.session.get_or_create("same-generation")
    assert resumed.id == named.id == first.id
    assert MaterializationLayout(tmp_path).generation == 11
    assert resumed.runs().items == ()


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
    before = session._runtime.store.db_path.read_bytes()
    with pytest.raises(IntegrityError) as caught:
        session.artifact("artifact_old")
    assert caught.value.stage in {"publication", "terminal_output"}
    assert session._runtime.store.db_path.read_bytes() == before
    with pytest.raises(ArtifactNotFoundError):
        session.artifact("missing")
    with sqlite3.connect(session._runtime.store.db_path) as connection:
        assert connection.execute(
            "SELECT descriptor_payload FROM dataset_artifacts"
        ).fetchone() == ("not even a decodable graph",)


def test_old_store_is_rejected_without_rewriting_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    path = tmp_path / ".marivo/analysis/generations/v9/session_store.db"
    path.parent.mkdir(parents=True)
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version=8")
    before = path.read_bytes()
    files = tuple(path.parent.iterdir())
    with pytest.raises(IntegrityError) as caught:
        mv.session.resume("old-session")
    assert caught.value.stage == "store_generation"
    assert path.read_bytes() == before and tuple(path.parent.iterdir()) == files
    assert not (path.parent.parent / "v11").exists()


def test_new_help_targets_resolve_and_disclose_phase_boundary(capsys) -> None:
    for target in (
        "analysis.session.observe",
        "analysis.Query",
        "analysis.Query.contract",
        "analysis.Query.execute",
        "analysis.session.artifact",
        "analysis.Artifact",
        "analysis.Artifact.show",
        "analysis.Artifact.to_pandas",
        "analysis.Artifact.findings",
        "analysis.Artifact.evidence_digest",
    ):
        marivo.help(target)
        assert target in capsys.readouterr().out
    marivo.help(mv.Artifact)
    rendered = capsys.readouterr().out
    assert "without DSL or contract" in rendered
    assert "committed" in rendered


def test_execute_help_discloses_actual_effects_for_string_and_callable(capsys) -> None:
    for target in ("analysis.Query.execute", mv.Query.execute):
        marivo.help(target)
        text = capsys.readouterr().out
        assert "new Run" in text and "reads sources" in text
        assert "atomically publishes" in text and "Store 11" in text
        assert "committed" in text or "output" in text
        assert "fails before data reads" not in text


@pytest.mark.parametrize("name", ("show", "to_pandas", "findings", "evidence_digest"))
def test_artifact_read_help_discloses_committed_reading_for_string_and_callable(
    capsys, name
) -> None:
    for target in (f"analysis.Artifact.{name}", getattr(mv.Artifact, name)):
        marivo.help(target)
        text = capsys.readouterr().out
        assert "committed" in text or "output" in text
        assert "unavailable until" not in text


@pytest.mark.parametrize(
    ("value", "target", "fields"),
    (
        (
            OutputEvidence("check", "Output checked.", (("rows", 1),)),
            "runtime.OutputEvidence",
            ("kind:", "summary:", "values:", "coordinates:"),
        ),
        (
            OutputCoordinate("key.0", 1),
            "runtime.OutputCoordinate",
            ("slot_id:", "value:"),
        ),
    ),
)
def test_terminal_record_help_resolves_nested_types_and_methods(value, target, fields, capsys):
    rendered = []
    for form in (f"analysis.{target}", type(value), value):
        marivo.help(form)
        text = capsys.readouterr().out
        assert f"analysis.{target}" in text
        assert all(field in text for field in fields)
        rendered.append(text)
    assert rendered[0] == rendered[1]
    for method in (value.show, value.render):
        marivo.help(method)
        assert "analysis.runtime.values." in capsys.readouterr().out
    assert type(value).__name__ not in mv.__all__
