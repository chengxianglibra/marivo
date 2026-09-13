"""Public source wiring, fixed target selection and exact generation admission."""

import sqlite3
from pathlib import Path

import pytest

import marivo.analysis as mv
import marivo.datasource as md
import marivo.semantic as ms
from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.analysis.errors import ArtifactNotFoundError, SessionNotFoundError
from marivo.analysis.materialization.errors import IntegrityError
from marivo.analysis.materialization.layout import MaterializationLayout


def test_new_public_session_preserves_eager_store_and_rejects_old_identities(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    old = tmp_path / ".marivo" / "analysis" / "session_store.db"
    old.parent.mkdir(parents=True)
    payload = b"private-old-store-canary: never decode as Dataset authority"
    old.write_bytes(payload)
    session = mv.session.get_or_create("fresh-generation", report_timezone="UTC")
    assert session.runs().items == ()
    assert session._runtime.store.db_path == MaterializationLayout(tmp_path).store_db
    with pytest.raises(ArtifactNotFoundError):
        session.artifact("old-artifact")
    with pytest.raises(SessionNotFoundError):
        mv.session.resume("old-session", by="id")
    assert old.read_bytes() == payload
    assert session.runs().items == ()


def test_public_construction_has_no_datasource_io_and_rejects_cross_session(
    authoring_evidence_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(authoring_evidence_project)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("logical construction performed datasource I/O")

    monkeypatch.setattr(md, "connect", forbidden)
    first = mv.session.get_or_create("first", report_timezone="UTC")
    second = mv.session.get_or_create("second", report_timezone="UTC")
    population = first.population(ms.ref.entity("sales.orders"))
    current = first.observe(ms.ref.metric("sales.revenue"), population=population).aggregate()
    baseline = second.observe(ms.ref.metric("sales.revenue")).aggregate()
    assert isinstance(current, mv.LogicalMetricDataset)
    assert not hasattr(current, "show")
    assert not hasattr(current, "to_pandas")
    with pytest.raises(DatasetConstructionError):
        current.compare(baseline)
    assert first.runs().items == ()
    assert second.runs().items == ()
    assert not first._runtime.statistics.statements
    assert not second._runtime.statistics.statements


@pytest.mark.parametrize("version", [0, 2, 4])
@pytest.mark.parametrize("entry", ["get_or_create", "resume", "current"])
def test_public_session_rejects_existing_store_without_modifying_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, version: int, entry: str
) -> None:
    monkeypatch.chdir(tmp_path)
    path = MaterializationLayout(tmp_path).store_db
    path.parent.mkdir(parents=True)
    with sqlite3.connect(path) as connection:
        connection.execute(f"PRAGMA user_version={version}")
    before = path.read_bytes()
    files = tuple(path.parent.iterdir())
    with pytest.raises(IntegrityError) as caught:
        if entry == "get_or_create":
            mv.session.get_or_create("new")
        elif entry == "resume":
            mv.session.resume("old")
        else:
            mv.session.current()
    assert caught.value.stage == "store_generation"
    assert "new named Session" in str(caught.value)
    assert path.read_bytes() == before
    assert tuple(path.parent.iterdir()) == files


@pytest.mark.runtime
def test_public_default_local_execute_and_source_offline_cold_resume(
    authoring_evidence_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(authoring_evidence_project)
    first = mv.session.get_or_create("cold", report_timezone="UTC")
    result = first.observe(ms.ref.metric("sales.revenue")).aggregate().execute()
    original = result.to_pandas()
    assert 751.5 in original.iloc[0].tolist()
    artifact = result.state.artifact_ref
    (authoring_evidence_project / "warehouse.duckdb").rename(
        authoring_evidence_project / "warehouse.offline"
    )
    recovered = mv.session.resume(first.id, by="id")
    loaded = recovered.artifact(artifact)
    assert loaded.to_pandas().equals(original)
    assert len(recovered.runs().items) == 1
    assert not recovered._runtime.statistics.statements
    assert not tuple((authoring_evidence_project / ".marivo").rglob("*.duckdb"))


@pytest.mark.runtime
def test_public_runtime_metric_executes_and_projects_after_cold_resume(
    authoring_evidence_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(authoring_evidence_project)
    first = mv.session.get_or_create("runtime-metric", report_timezone="UTC")
    measure = first.catalog.require(ms.ref.measure("sales.orders.amount")).ref
    expression = mv.runtime_metric.aggregate(measure, agg="sum", label="runtime_total")
    logical = first.observe(expression).aggregate()
    assert isinstance(logical, mv.LogicalMetricDataset)
    assert first.runs().items == ()
    assert not first._runtime.statistics.statements
    output = logical.execute()
    assert output.to_pandas()["runtime_total"].tolist() == [751.5]
    (authoring_evidence_project / "warehouse.duckdb").rename(
        authoring_evidence_project / "warehouse.offline"
    )
    cold = mv.session.resume(first.id, by="id")
    loaded = cold.artifact(output.state.artifact_ref)
    assert isinstance(loaded, mv.MaterializedMetricDataset)
    from marivo.analysis.materialization import admission

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("cold projection attempted source execution")

    for name in (
        "_build_backend_from_effective",
        "_effective_kwargs",
        "require_profile_for_backend_type",
        "compile_dataset",
    ):
        monkeypatch.setattr(admission, name, forbidden)
    selected = loaded.metric(loaded.fields.get("runtime_total"))
    assert selected.execute().to_pandas()["runtime_total"].tolist() == [751.5]
    assert cold._runtime.statistics.source_fences == 0
    assert not tuple((authoring_evidence_project / ".marivo").rglob("*.duckdb"))


def test_public_calendar_snapshot_is_captured_before_pure_construction(
    semantic_project_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from marivo.analysis.observation.contracts import owner_of
    from marivo.semantic.catalog import SemanticCatalog
    from tests.shared_fixtures import (
        fiscal_analysis_project_files,
        publish_fiscal_calendar_artifact,
    )

    files = fiscal_analysis_project_files()
    files["sales/metrics.py"] = files["sales/metrics.py"].replace(
        "source=md.table('events')",
        "source=md.table('events', columns={'user_id': md.source_column('user_id', data_type='int64'), 'amount': md.source_column('amount', data_type='float64'), 'event_date': md.source_column('event_date', data_type='date')}), primary_key=['user_id']",
    )
    files["sales/calendar.py"] = files["sales/calendar.py"].replace(
        "source=md.table('calendar')",
        "source=md.table('calendar', columns={'calendar_date': md.source_column('calendar_date', data_type='date'), 'fiscal_week': md.source_column('fiscal_week', data_type='string'), 'fiscal_month': md.source_column('fiscal_month', data_type='string')}), primary_key=['calendar_date']",
    )
    catalog = SemanticCatalog(semantic_project_factory(files))
    publish_fiscal_calendar_artifact(catalog)
    monkeypatch.chdir(catalog.workspace_dir)
    session = mv.session.get_or_create("calendar", report_timezone="UTC")
    calendar = session.catalog.require(ms.ref.period_calendar("sales.fiscal"))
    grain = calendar.grain("fiscal_week")
    scope = calendar.period("fiscal_week", "M1-W1")
    metric = ms.ref.metric("sales.gmv")
    axis = ms.ref.time_dimension("sales.events.event_date")

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("construction reread the certified calendar")

    from marivo._temporal import TemporalSnapshotStore

    monkeypatch.setattr(TemporalSnapshotStore, "inspect_current", forbidden)
    logical = (
        session.observe(metric, time_scope=scope).with_time_axis(axis, grain=grain).aggregate()
    )
    assert isinstance(logical, mv.LogicalMetricDataset)
    snapshots = owner_of(logical).period_calendar_snapshots
    assert len(snapshots) == 1
    assert snapshots[0].period_scope("fiscal_week", "M1-W1") == scope
    assert session.runs().items == ()
