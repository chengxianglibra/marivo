"""Session class: store-backed jobs and frame summaries."""

import textwrap
from datetime import UTC, datetime

import duckdb
import pytest

from marivo.analysis.calendar.loader import CalendarCache
from marivo.analysis.errors import JobNotFoundError
from marivo.analysis.session._layout import PersistenceLayout
from marivo.analysis.session._runtime import _build_connection_runtime, persist_job_record
from marivo.analysis.session._store import SessionStore
from marivo.analysis.session.core import JobSummary, Session
from marivo.semantic.catalog import SemanticCatalog
from marivo.semantic.reader import SemanticProject
from tests.shared_fixtures import make_metric_frame


def _now():
    return datetime(2026, 5, 24, 10, 0, 0, tzinfo=UTC)


def _session(tmp_path, *, read_only: bool = False) -> Session:
    layout = PersistenceLayout(project_root=tmp_path, session_id="sess_t01")
    store = SessionStore(project_root=tmp_path)
    # Insert a session row with the known ID so foreign key constraints pass.
    with store._connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO sessions (id, name, question, cwd, default_calendar, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "sess_t01",
                "demo",
                "q",
                str(tmp_path),
                None,
                "2026-05-24T10:00:00+00:00",
                "2026-05-24T10:00:00+00:00",
            ),
        )
    return Session(
        id="sess_t01",
        name="demo",
        question="q",
        cwd=tmp_path,
        project_root=tmp_path,
        created_at=_now(),
        updated_at=_now(),
        connection_runtime=_build_connection_runtime(
            tmp_path,
            None if read_only else {"fake": lambda: object()},
            None,
            use_datasources=False,
        ),
        layout=layout,
        semantic_catalog=SemanticCatalog(SemanticProject(workspace_dir=tmp_path)),
        store=store,
    )


def test_session_is_read_only_when_no_factory(tmp_path):
    assert _session(tmp_path, read_only=True).is_read_only is True


def test_session_is_not_read_only_with_factory(tmp_path):
    assert _session(tmp_path, read_only=False).is_read_only is False


def test_session_jobs_lists_records_sorted_by_started_at(tmp_path):
    s = _session(tmp_path)
    persist_job_record(
        s,
        {
            "id": "job_two",
            "session_id": "sess_t01",
            "intent": "observe",
            "params": {},
            "input_frame_refs": [],
            "output_frame_ref": "f2",
            "started_at": "2026-05-24T10:05:00+00:00",
            "finished_at": "2026-05-24T10:05:01+00:00",
            "duration_ms": 1000,
            "status": "succeeded",
            "error": None,
            "semantic_project_root": "/p",
            "semantic_model": "sales",
        },
    )
    persist_job_record(
        s,
        {
            "id": "job_one",
            "session_id": "sess_t01",
            "intent": "observe",
            "params": {},
            "input_frame_refs": [],
            "output_frame_ref": "f1",
            "started_at": "2026-05-24T10:00:00+00:00",
            "finished_at": "2026-05-24T10:00:01+00:00",
            "duration_ms": 1000,
            "status": "succeeded",
            "error": None,
            "semantic_project_root": "/p",
            "semantic_model": "sales",
        },
    )
    summaries = s.jobs()
    assert [j.id for j in summaries] == ["job_one", "job_two"]
    assert isinstance(summaries[0], JobSummary)


def test_session_job_raises_job_not_found_from_store_absence(tmp_path):
    s = _session(tmp_path)
    with pytest.raises(JobNotFoundError) as exc_info:
        s.job("nonexistent_job")
    assert "nonexistent_job" in exc_info.value.message


def test_session_frame_summaries_returns_only_registered_artifacts(tmp_path):
    s = _session(tmp_path)
    import pandas as pd

    # make_metric_frame uses persist_frame which registers in the store.
    frame = make_metric_frame(
        pd.DataFrame({"value": [1.0]}),
        metric_id="sales.revenue",
        axes={},
        measure={"name": "value"},
        semantic_kind="scalar",
        semantic_model="sales",
        session=s,
    )

    records = s.frame_summaries()
    assert len(records) == 1
    assert records[0].ref == frame.ref
    assert records[0].kind == "metric_frame"
    assert records[0].metric_id == "sales.revenue"

    # Write a frame directory without registering in the store — it should be invisible.
    orphan_dir = s._layout.frames_dir / "orphan_001"
    orphan_dir.mkdir(parents=True)
    (orphan_dir / "meta.json").write_text(
        '{"ref": "orphan_001", "kind": "metric_frame", "metric_id": "orphan.metric"}'
    )
    assert len(s.frame_summaries()) == 1


def test_session_frame_summaries_sorted_by_created_at_then_ref(tmp_path):
    s = _session(tmp_path)
    import pandas as pd

    frame_a = make_metric_frame(
        pd.DataFrame({"value": [1.0]}),
        metric_id="sales.revenue",
        axes={},
        measure={"name": "value"},
        semantic_kind="scalar",
        semantic_model="sales",
        session=s,
    )
    frame_b = make_metric_frame(
        pd.DataFrame({"value": [2.0]}),
        metric_id="sales.revenue",
        axes={},
        measure={"name": "value"},
        semantic_kind="scalar",
        semantic_model="sales",
        session=s,
    )
    records = s.frame_summaries()
    assert len(records) == 2
    # Frames are sorted by created_at then ref.
    # The two frames were created sequentially, so they should be in creation order.
    assert records[0].ref == frame_a.ref
    assert records[1].ref == frame_b.ref


def test_session_close_closes_runtime_connections(tmp_path):
    s = _session(tmp_path)
    s._connection_runtime.session_backend("fake")
    assert s._connection_runtime.service._session_backends
    s.close()
    assert s._connection_runtime.service._session_backends == {}


def test_session_initializes_calendar_cache(tmp_path):
    s = _session(tmp_path)
    assert isinstance(s._calendars, CalendarCache)


def test_session_public_fields_are_read_only(tmp_path):
    s = _session(tmp_path)
    with pytest.raises(AttributeError):
        s.id = "other"
    with pytest.raises(AttributeError):
        s.name = "other"
    with pytest.raises(AttributeError):
        s.created_at = _now()


def test_session_exposes_catalog_property(tmp_path, monkeypatch):
    import marivo.analysis as mv
    from marivo.semantic.catalog import SemanticCatalog

    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create(name="catalog_session", use_datasources=False)

    assert isinstance(session.catalog, SemanticCatalog)
    assert session.catalog.workspace_dir == tmp_path


def test_session_catalog_loads_external_semantic_layer(tmp_path, monkeypatch):
    import textwrap

    import marivo.analysis as mv

    project_root = tmp_path / "project"
    external_models = tmp_path / "external" / "models"
    project_root.mkdir()
    (project_root / "marivo.toml").write_text(
        textwrap.dedent(
            """
            [project]
            name = "demo"

            [semantic]
            layer_paths = ["../external/models"]
            """
        ),
        encoding="utf-8",
    )
    for models_root, datasource, domain, entity, metric in (
        (project_root / "models", "local_warehouse", "sales", "orders", "revenue"),
        (external_models, "external_warehouse", "finance", "refunds", "refunds_total"),
    ):
        datasource_dir = models_root / "datasources"
        semantic_dir = models_root / "semantic" / domain
        datasource_dir.mkdir(parents=True, exist_ok=True)
        semantic_dir.mkdir(parents=True, exist_ok=True)
        (datasource_dir / f"{datasource}.py").write_text(
            f"import marivo.datasource as md\nmd.duckdb(name={datasource!r}, path=':memory:')\n",
            encoding="utf-8",
        )
        (semantic_dir / "_domain.py").write_text(
            f"import marivo.semantic as ms\nms.domain(name={domain!r}, owner='Mina Zhang')\n",
            encoding="utf-8",
        )
        (semantic_dir / "objects.py").write_text(
            textwrap.dedent(
                f"""
                import marivo.datasource as md
                import marivo.semantic as ms

                source = md.ref("datasource.{datasource}")
                rows = ms.entity(name={entity!r}, datasource=source, source=md.table({entity!r}))

                @ms.metric(entities=[rows], additivity="additive")
                def {metric}(table):
                    return table.amount.sum()
                """
            ),
            encoding="utf-8",
        )

    monkeypatch.chdir(project_root)

    session = mv.session.get_or_create(name="external_layer_session")

    assert session.catalog.get("metric.finance.refunds_total").ref.id == "finance.refunds_total"


def test_session_observe_uses_external_layer_datasource(tmp_path, monkeypatch):
    import marivo.analysis as mv
    import marivo.analysis.session as session_attach

    project_root = tmp_path / "project"
    external_models = tmp_path / "external" / "models"
    db_path = tmp_path / "warehouse.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute("CREATE TABLE refunds (amount DOUBLE)")
    con.execute("INSERT INTO refunds VALUES (100.0), (50.0)")
    con.close()
    project_root.mkdir()
    (project_root / "marivo.toml").write_text(
        textwrap.dedent(
            """
            [project]
            name = "demo"

            [semantic]
            layer_paths = ["../external/models"]
            """
        ),
        encoding="utf-8",
    )
    datasource_dir = external_models / "datasources"
    semantic_dir = external_models / "semantic" / "finance"
    datasource_dir.mkdir(parents=True, exist_ok=True)
    semantic_dir.mkdir(parents=True, exist_ok=True)
    (datasource_dir / "warehouse.py").write_text(
        f"import marivo.datasource as md\nmd.duckdb(name='warehouse', path={str(db_path)!r})\n",
        encoding="utf-8",
    )
    (semantic_dir / "_domain.py").write_text(
        "import marivo.semantic as ms\nms.domain(name='finance', owner='Mina Zhang')\n",
        encoding="utf-8",
    )
    (semantic_dir / "objects.py").write_text(
        textwrap.dedent(
            """
            import marivo.datasource as md
            import marivo.semantic as ms

            source = md.ref("datasource.warehouse")
            rows = ms.entity(name="refunds", datasource=source, source=md.table("refunds"))

            @ms.metric(entities=[rows], additivity="additive")
            def refunds_total(table):
                return table.amount.sum()
            """
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(project_root)
    session_attach._reset_process_state()

    session = mv.session.get_or_create(name="external_layer_observe")
    metric = session.catalog.get("metric.finance.refunds_total")
    frame = session.observe(metric)

    assert frame.meta.metric_id == "finance.refunds_total"
    assert frame.to_pandas()["value"].tolist() == [150.0]


def test_session_close_closes_connection_runtime(tmp_path):
    from datetime import UTC, datetime

    from marivo.analysis.session._connections import AnalysisConnectionRuntime
    from marivo.analysis.session._layout import PersistenceLayout
    from marivo.analysis.session._store import SessionStore
    from marivo.datasource.runtime import DatasourceConnectionService
    from marivo.semantic.catalog import SemanticCatalog
    from marivo.semantic.reader import SemanticProject

    class Runtime(AnalysisConnectionRuntime):
        def __init__(self):
            super().__init__(
                DatasourceConnectionService(project_root=tmp_path, use_datasources=False)
            )
            self.closed = False

        def close_all(self):
            self.closed = True

    runtime = Runtime()
    project = SemanticProject(workspace_dir=tmp_path)
    session = Session(
        id="s1",
        name="n",
        question=None,
        cwd=tmp_path,
        project_root=tmp_path,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        connection_runtime=runtime,
        layout=PersistenceLayout(project_root=tmp_path, session_id="s1"),
        semantic_catalog=SemanticCatalog(project),
        store=SessionStore(project_root=tmp_path),
    )

    session.close()

    assert runtime.closed


def test_session_constructor_rejects_old_runtime_keywords(tmp_path):
    from marivo.analysis.session._connections import AnalysisConnectionRuntime
    from marivo.analysis.session._layout import PersistenceLayout
    from marivo.analysis.session._store import SessionStore
    from marivo.datasource.runtime import DatasourceConnectionService
    from marivo.semantic.catalog import SemanticCatalog
    from marivo.semantic.reader import SemanticProject

    kwargs = {
        "id": "s1",
        "name": "n",
        "question": None,
        "cwd": tmp_path,
        "project_root": tmp_path,
        "created_at": _now(),
        "updated_at": _now(),
        "connection_runtime": AnalysisConnectionRuntime(
            DatasourceConnectionService(project_root=tmp_path, use_datasources=False)
        ),
        "layout": PersistenceLayout(project_root=tmp_path, session_id="s1"),
        "semantic_catalog": SemanticCatalog(SemanticProject(workspace_dir=tmp_path)),
        "store": SessionStore(project_root=tmp_path),
    }

    with pytest.raises(TypeError):
        Session(**kwargs, backend_factory=lambda name: object())

    with pytest.raises(TypeError):
        Session(**kwargs, semantic_project=SemanticProject(workspace_dir=tmp_path))


def test_persisted_frame_records_content_hash_in_meta_store_and_state(tmp_path):
    s = _session(tmp_path)
    import json

    import pandas as pd

    frame = make_metric_frame(
        pd.DataFrame({"bucket_start": ["2026-06-18"], "value": [1.0]}),
        metric_id="sales.revenue",
        axes={},
        measure={"name": "value"},
        semantic_kind="time_series",
        semantic_model="sales",
        session=s,
    )

    assert frame.state.content_hash is not None
    assert frame.state.content_hash.startswith("sha256:")

    row = s._store.get_artifact(s.id, frame.ref)
    assert row is not None
    assert row["content_hash"] == frame.state.content_hash

    meta_path = s.project_root / row["meta_path"]
    meta_payload = json.loads(meta_path.read_text())
    assert meta_payload["content_hash"] == frame.state.content_hash

    loaded = s.get_frame(frame.ref)
    assert loaded.state.content_hash == frame.state.content_hash

    [summary] = s.frame_summaries()
    assert summary.content_hash == frame.state.content_hash


# ---------------------------------------------------------------------------
# Session.validate_semantic_handoff
# ---------------------------------------------------------------------------

_DOMAIN_PY = textwrap.dedent("""\
    import marivo.semantic as ms
    ms.domain(name="sales", owner="Mina Zhang", default=True)
""")

_READY_OBJECTS_PY = textwrap.dedent("""\
    import marivo.datasource as md
    import marivo.semantic as ms

    orders = ms.entity(
        name="orders",
        datasource=md.ref("datasource.warehouse"),
        source=md.table("orders"),
        primary_key=["order_id"],
        ai_context=ms.ai_context(
            business_definition="One row per paid order.",
            guardrails=["Exclude test and internal orders."],
        ),
    )

    @ms.dimension(
        entity=orders,
        ai_context=ms.ai_context(
            business_definition="Gross order amount in USD.",
            guardrails=["USD only; do not mix currencies."],
        ),
    )
    def amount(table):
        return table.amount

    @ms.time_dimension(
        entity=orders,
        granularity="day",
        parse=ms.timestamp(timezone="UTC"),
        ai_context=ms.ai_context(
            business_definition="Timestamp when the order was created.",
            guardrails=["Assume UTC; do not reinterpret in local time."],
        ),
    )
    def created_at(table):
        return table.created_at

    @ms.metric(
        entities=[orders],
        additivity="additive",
        ai_context=ms.ai_context(
            business_definition="Sum of order amount.",
            guardrails=["Not comparable across currencies without normalization."],
        ),
    )
    def total_amount(table):
        return table.amount.sum()
""")


def _session_with_catalog(semantic_project_factory, tmp_path):
    """Build a Session backed by a loaded semantic project with ready refs."""
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/objects.py": _READY_OBJECTS_PY,
        }
    )
    from marivo.semantic.catalog import SemanticCatalog

    catalog = SemanticCatalog(project)
    from tests.shared_fixtures import build_session_over_catalog

    return build_session_over_catalog(catalog, tmp_path)


def _make_handoff(session, **overrides):
    """Build a valid SemanticToAnalysisHandoff for the given session."""
    from marivo._boundaries.semantic_analysis import SemanticToAnalysisHandoff
    from marivo.introspection.live.model import EnvironmentFingerprint, LiveHelpTarget
    from marivo.semantic.catalog import SemanticKind
    from marivo.semantic.refs import make_ref

    metric_ref = make_ref("sales.total_amount", SemanticKind.METRIC)
    kwargs: dict[str, object] = {
        "help_target": LiveHelpTarget(surface="semantic"),
        "ready_refs": (metric_ref,),
        "project_fingerprint": session._project_fingerprint(),
        "catalog_fingerprint": session._catalog_fingerprint(),
        "environment_fingerprint": EnvironmentFingerprint.current(),
        "readiness_status": "ready",
        "warning_ids": (),
        "preview_evidence_ids": (),
        "caveats": (),
    }
    kwargs.update(overrides)
    return SemanticToAnalysisHandoff(**kwargs)


def test_validate_semantic_handoff_success_returns_receipt(
    semantic_project_factory, tmp_path, monkeypatch
):
    from marivo._boundaries.semantic_analysis import SemanticHandoffReceipt
    from marivo.semantic.readiness import ReadinessInputSummary, ReadinessReport

    session = _session_with_catalog(semantic_project_factory, tmp_path)

    ready_report = ReadinessReport(
        status="ready",
        analysis_ready_refs=("sales.total_amount",),
        blockers=(),
        warnings=(),
        input_summary=ReadinessInputSummary(
            datasources=("warehouse",),
            refs=("sales.total_amount",),
            tables=("sales.orders",),
        ),
        checked_at="2026-07-14T00:00:00Z",
    )
    monkeypatch.setattr(session._catalog, "readiness", lambda **kw: ready_report)

    handoff = _make_handoff(session)

    receipt = session.validate_semantic_handoff(handoff)

    assert isinstance(receipt, SemanticHandoffReceipt)
    assert receipt.ready_refs == handoff.ready_refs
    assert receipt.project_fingerprint == session._project_fingerprint()
    assert receipt.catalog_fingerprint == session._catalog_fingerprint()
    assert receipt.readiness_status == "ready"
    assert receipt.warning_ids == ()
    assert receipt.preview_evidence_ids == ()
    assert receipt.caveats == ()


def test_validate_semantic_handoff_environment_mismatch_raises(semantic_project_factory, tmp_path):
    from marivo.analysis.errors import AnalysisError
    from marivo.introspection.live.model import EnvironmentFingerprint

    session = _session_with_catalog(semantic_project_factory, tmp_path)
    stale_env = EnvironmentFingerprint(
        marivo_version="0.0.0",
        python_executable="/fake/python",
        package_path="/fake/marivo",
    )
    handoff = _make_handoff(session, environment_fingerprint=stale_env)

    with pytest.raises(AnalysisError) as exc_info:
        session.validate_semantic_handoff(handoff)

    assert exc_info.value.repair is not None
    assert exc_info.value.repair.kind == "environment"
    assert "Environment has changed" in exc_info.value.message


def test_validate_semantic_handoff_stale_project_fingerprint_raises(
    semantic_project_factory, tmp_path
):
    from marivo.analysis.errors import AnalysisError

    session = _session_with_catalog(semantic_project_factory, tmp_path)
    handoff = _make_handoff(session, project_fingerprint="stale_project_fp")

    with pytest.raises(AnalysisError) as exc_info:
        session.validate_semantic_handoff(handoff)

    assert exc_info.value.repair is not None
    assert exc_info.value.repair.kind == "semantic_handoff"
    assert exc_info.value.repair.semantic_handoff is not None
    assert "Project or catalog state" in exc_info.value.message


def test_validate_semantic_handoff_stale_catalog_fingerprint_raises(
    semantic_project_factory, tmp_path
):
    from marivo.analysis.errors import AnalysisError

    session = _session_with_catalog(semantic_project_factory, tmp_path)
    handoff = _make_handoff(session, catalog_fingerprint="stale_catalog_fp")

    with pytest.raises(AnalysisError) as exc_info:
        session.validate_semantic_handoff(handoff)

    assert exc_info.value.repair is not None
    assert exc_info.value.repair.kind == "semantic_handoff"
    assert exc_info.value.repair.semantic_handoff is not None


def test_validate_semantic_handoff_missing_ref_raises(semantic_project_factory, tmp_path):
    from marivo.analysis.errors import AnalysisError
    from marivo.semantic.catalog import SemanticKind
    from marivo.semantic.refs import make_ref

    session = _session_with_catalog(semantic_project_factory, tmp_path)
    missing_ref = make_ref("sales.nonexistent", SemanticKind.METRIC)
    handoff = _make_handoff(session, ready_refs=(missing_ref,))

    with pytest.raises(AnalysisError) as exc_info:
        session.validate_semantic_handoff(handoff)

    assert exc_info.value.repair is not None
    assert exc_info.value.repair.kind == "semantic_handoff"
    assert exc_info.value.repair.semantic_handoff is not None
    assert "sales.nonexistent" in exc_info.value.message


def test_validate_semantic_handoff_wrong_kind_raises(semantic_project_factory, tmp_path):
    from marivo.analysis.errors import AnalysisError
    from marivo.semantic.catalog import SemanticKind
    from marivo.semantic.refs import make_ref

    session = _session_with_catalog(semantic_project_factory, tmp_path)
    wrong_kind_ref = make_ref("sales.total_amount", SemanticKind.DIMENSION)
    handoff = _make_handoff(session, ready_refs=(wrong_kind_ref,))

    with pytest.raises(AnalysisError) as exc_info:
        session.validate_semantic_handoff(handoff)

    assert exc_info.value.repair is not None
    assert exc_info.value.repair.kind == "semantic_handoff"
    assert exc_info.value.repair.semantic_handoff is not None
    assert "kind" in exc_info.value.message.lower()


def test_validate_semantic_handoff_blocked_readiness_raises(semantic_project_factory, tmp_path):
    from marivo.analysis.errors import AnalysisError

    session = _session_with_catalog(semantic_project_factory, tmp_path)
    handoff = _make_handoff(session, readiness_status="ready")

    with pytest.raises(AnalysisError) as exc_info:
        session.validate_semantic_handoff(handoff)

    assert exc_info.value.repair is not None
    assert exc_info.value.repair.kind == "semantic_handoff"
    assert exc_info.value.repair.semantic_handoff is not None
    assert "blocked" in exc_info.value.message.lower()


def test_validate_semantic_handoff_warning_id_mismatch_raises(
    semantic_project_factory, tmp_path, monkeypatch
):
    from marivo.analysis.errors import AnalysisError
    from marivo.semantic.readiness import ReadinessInputSummary, ReadinessIssue, ReadinessReport

    session = _session_with_catalog(semantic_project_factory, tmp_path)
    ready_report = ReadinessReport(
        status="ready_with_warnings",
        analysis_ready_refs=("sales.total_amount",),
        blockers=(),
        warnings=(
            ReadinessIssue(
                kind="fragile_string_ref",
                severity="warning",
                refs=("sales.orders",),
                message="string ref used",
                repair=None,
            ),
        ),
        input_summary=ReadinessInputSummary(
            datasources=("warehouse",),
            refs=("sales.total_amount",),
            tables=("sales.orders",),
        ),
        checked_at="2026-07-14T00:00:00Z",
    )
    monkeypatch.setattr(session._catalog, "readiness", lambda **kw: ready_report)
    handoff = _make_handoff(
        session,
        readiness_status="ready_with_warnings",
        warning_ids=("different_warning",),
    )

    with pytest.raises(AnalysisError) as exc_info:
        session.validate_semantic_handoff(handoff)

    assert exc_info.value.repair is not None
    assert exc_info.value.repair.kind == "semantic_handoff"
    assert exc_info.value.repair.semantic_handoff is not None


def test_validate_semantic_handoff_missing_preview_evidence_raises(
    semantic_project_factory, tmp_path, monkeypatch
):
    from marivo.analysis.errors import AnalysisError
    from marivo.semantic.readiness import ReadinessInputSummary, ReadinessReport

    session = _session_with_catalog(semantic_project_factory, tmp_path)
    ready_report = ReadinessReport(
        status="ready",
        analysis_ready_refs=("sales.total_amount",),
        blockers=(),
        warnings=(),
        input_summary=ReadinessInputSummary(
            datasources=("warehouse",),
            refs=("sales.total_amount",),
            tables=("sales.orders",),
        ),
        checked_at="2026-07-14T00:00:00Z",
    )
    monkeypatch.setattr(session._catalog, "readiness", lambda **kw: ready_report)
    handoff = _make_handoff(session, preview_evidence_ids=("nonexistent_evidence_id",))

    with pytest.raises(AnalysisError) as exc_info:
        session.validate_semantic_handoff(handoff)

    assert exc_info.value.repair is not None
    assert exc_info.value.repair.kind == "semantic_handoff"
    assert exc_info.value.repair.semantic_handoff is not None
    assert "nonexistent_evidence_id" in exc_info.value.message


def test_validate_semantic_handoff_does_not_persist_receipt(
    semantic_project_factory, tmp_path, monkeypatch
):
    from marivo.semantic.readiness import ReadinessInputSummary, ReadinessReport

    session = _session_with_catalog(semantic_project_factory, tmp_path)
    ready_report = ReadinessReport(
        status="ready",
        analysis_ready_refs=("sales.total_amount",),
        blockers=(),
        warnings=(),
        input_summary=ReadinessInputSummary(
            datasources=("warehouse",),
            refs=("sales.total_amount",),
            tables=("sales.orders",),
        ),
        checked_at="2026-07-14T00:00:00Z",
    )
    monkeypatch.setattr(session._catalog, "readiness", lambda **kw: ready_report)
    handoff = _make_handoff(session)

    session.validate_semantic_handoff(handoff)

    assert len(session._store.list_artifacts(session.id)) == 0


def test_validate_semantic_handoff_does_not_mutate_session_state(
    semantic_project_factory, tmp_path, monkeypatch
):
    from marivo.semantic.readiness import ReadinessInputSummary, ReadinessReport

    session = _session_with_catalog(semantic_project_factory, tmp_path)
    ready_report = ReadinessReport(
        status="ready",
        analysis_ready_refs=("sales.total_amount",),
        blockers=(),
        warnings=(),
        input_summary=ReadinessInputSummary(
            datasources=("warehouse",),
            refs=("sales.total_amount",),
            tables=("sales.orders",),
        ),
        checked_at="2026-07-14T00:00:00Z",
    )
    monkeypatch.setattr(session._catalog, "readiness", lambda **kw: ready_report)
    handoff = _make_handoff(session)

    original_updated_at = session.updated_at
    original_name = session.name

    session.validate_semantic_handoff(handoff)

    assert session.updated_at == original_updated_at
    assert session.name == original_name


def test_validate_semantic_handoff_real_producer_round_trip(
    semantic_project_factory, tmp_path, monkeypatch
):
    """Real producer -> validator round trip with no monkeypatched readiness.

    Builds the ready revenue catalog, persists a fresh preview check, and runs
    the real ``catalog.readiness`` to attach the producer handoff. The handoff
    is then validated through the analysis Session over the same catalog and
    must yield a ``SemanticHandoffReceipt`` mirroring the handed-off facts.
    """
    from marivo._boundaries.semantic_analysis import SemanticHandoffReceipt
    from tests.test_semantic_analysis_handoff import (
        _ready_revenue_catalog_and_snapshot,
        _session_for_catalog,
    )

    catalog, snapshot = _ready_revenue_catalog_and_snapshot(
        semantic_project_factory, tmp_path, monkeypatch
    )
    revenue = catalog.get("metric.sales.revenue")
    catalog.preview(revenue.ref, using=snapshot, limit=2)
    handoff = catalog.readiness(refs=[revenue.ref]).analysis_handoff
    assert handoff is not None

    session = _session_for_catalog(catalog, tmp_path)
    receipt = session.validate_semantic_handoff(handoff)

    assert isinstance(receipt, SemanticHandoffReceipt)
    ready_ids = [str(r) for r in receipt.ready_refs]
    assert "sales.revenue" in ready_ids
    assert receipt.readiness_status == handoff.readiness_status
    assert receipt.warning_ids == handoff.warning_ids
    assert receipt.preview_evidence_ids == ()
