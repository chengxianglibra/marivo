"""Phase 4: semantic-to-analysis handoff producer, privacy, and round-trip."""

from __future__ import annotations

import textwrap

import ibis
import pytest

from marivo.introspection.live.fingerprints import (
    catalog_fingerprint,
    project_fingerprint,
)

# Authoring source for an enriched, certification-ready ``metric.sales.revenue``
# backed by a real duckdb ``orders`` table. The metric carries
# ``business_definition`` and ``guardrails`` so the strict-enrichment readiness
# check passes (no ``missing_business_definition``/``missing_guardrails``
# blockers). Mirrors the ``_DATASETS_PY`` shape from ``test_semantic_catalog.py``
# but enriches every object so readiness can reach ``ready``.
_READY_REVENUE_DATASETS_PY = textwrap.dedent("""\
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
            business_definition="Region the order was placed in.",
            guardrails=["Use ISO region codes only."],
        ),
    )
    def region(table):
        return table.region

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
            business_definition="Sum of order amount in USD.",
            guardrails=["USD only; do not mix currencies."],
        ),
    )
    def revenue(table):
        return table.amount.sum()
""")

_READY_REVENUE_DOMAIN_PY = textwrap.dedent("""\
    import marivo.semantic as ms
    ms.domain(name="sales", owner="Mina Zhang", default=True)
""")


def _ready_revenue_catalog_and_snapshot(semantic_project_factory, tmp_path, monkeypatch):
    """Build a catalog whose ``metric.sales.revenue`` is analysis-ready.

    Creates a real duckdb ``orders`` table, authors an enriched
    ``metric.sales.revenue`` against it, and returns the catalog plus a
    matching ``DiscoverySnapshot`` so a preview check can be persisted. Run
    ``catalog.preview(metric.ref, using=snapshot, limit=2)`` before
    ``catalog.readiness`` to satisfy the preview-evidence gate.
    """
    import marivo.datasource as md
    from marivo.semantic.catalog import SemanticCatalog

    database_path = tmp_path / "warehouse.duckdb"
    backend = ibis.duckdb.connect(str(database_path))
    backend.con.execute(
        "CREATE TABLE orders (order_id INT, amount DOUBLE, region TEXT, created_at TIMESTAMP)"
    )
    backend.con.execute(
        "INSERT INTO orders VALUES (1, 100.0, 'US', '2025-01-01'), (2, 200.0, 'EU', '2025-01-02')"
    )
    backend.disconnect()

    project = semantic_project_factory(
        {
            "datasources/warehouse.py": (
                "import marivo.datasource as md\n"
                f"md.duckdb(name='warehouse', path={str(database_path)!r})\n"
            ),
            "sales/_domain.py": _READY_REVENUE_DOMAIN_PY,
            "sales/datasets.py": _READY_REVENUE_DATASETS_PY,
        }
    )
    monkeypatch.chdir(tmp_path)
    catalog = SemanticCatalog(project)
    snapshot = md.inspect(md.ref("datasource.warehouse"), md.table("orders")).sample(
        scope=md.unpruned(max_rows=2, timeout_seconds=30),
        columns=("order_id", "amount", "region", "created_at"),
    )
    return catalog, snapshot


def test_project_fingerprint_hashes_marivo_toml_and_models(tmp_path):
    (tmp_path / "marivo.toml").write_text("name = 'demo'\n", encoding="utf-8")
    models = tmp_path / "models"
    models.mkdir()
    (models / "a.py").write_text("x = 1\n", encoding="utf-8")
    (models / "b.py").write_text("y = 2\n", encoding="utf-8")

    fp = project_fingerprint(tmp_path)

    import hashlib

    # Each part is "relpath:<file contents>"; parts are joined by "\n". File
    # contents include their trailing newline, so the join produces a blank
    # line between consecutive entries. This matches the analysis
    # Session._project_fingerprint contract byte-for-byte.
    expected = hashlib.sha256(
        b"marivo.toml:name = 'demo'\n\nmodels/a.py:x = 1\n\nmodels/b.py:y = 2\n"
    ).hexdigest()
    assert fp == expected


def test_project_fingerprint_is_order_independent_and_stable(tmp_path):
    (tmp_path / "marivo.toml").write_text("name = 'demo'\n", encoding="utf-8")
    models = tmp_path / "models"
    models.mkdir()
    (models / "b.py").write_text("y = 2\n", encoding="utf-8")
    (models / "a.py").write_text("x = 1\n", encoding="utf-8")
    assert project_fingerprint(tmp_path) == project_fingerprint(tmp_path)


def test_catalog_fingerprint_hashes_sorted_ids_joined_by_pipe():
    assert catalog_fingerprint(["m.b", "m.a"]) == catalog_fingerprint(["m.a", "m.b"])
    import hashlib

    assert catalog_fingerprint(["m.a", "m.b"]) == hashlib.sha256(b"m.a|m.b").hexdigest()


def test_readiness_report_analysis_handoff_defaults_to_none():
    from marivo.semantic.readiness import ReadinessInputSummary, ReadinessReport

    report = ReadinessReport(
        status="blocked",
        analysis_ready_refs=(),
        blockers=(),
        warnings=(),
        input_summary=ReadinessInputSummary(datasources=(), refs=(), tables=()),
        checked_at="2026-07-15T00:00:00Z",
    )
    assert report.analysis_handoff is None
    assert report.to_dict()["analysis_handoff"] is None


def test_readiness_report_to_dict_masks_handoff_environment_paths():
    from marivo.introspection.live.model import (
        EnvironmentFingerprint,
        LiveHelpTarget,
        SemanticToAnalysisHandoff,
    )
    from marivo.semantic.readiness import ReadinessInputSummary, ReadinessReport

    fp = EnvironmentFingerprint.current()
    handoff = SemanticToAnalysisHandoff(
        help_target=LiveHelpTarget(surface="analysis", canonical_id="boundary.semantic_handoff"),
        ready_refs=(),
        project_fingerprint="proj",
        catalog_fingerprint="cat",
        environment_fingerprint=fp,
        readiness_status="ready",
    )
    report = ReadinessReport(
        status="ready",
        analysis_ready_refs=(),
        blockers=(),
        warnings=(),
        input_summary=ReadinessInputSummary(datasources=(), refs=(), tables=()),
        checked_at="2026-07-15T00:00:00Z",
        analysis_handoff=handoff,
    )

    payload = report.to_dict()["analysis_handoff"]
    rendered_env = payload["environment_fingerprint"]
    assert fp.python_executable not in rendered_env
    assert fp.package_path not in rendered_env
    assert fp.marivo_version in rendered_env
    assert "fingerprint" in rendered_env


def test_readiness_produces_handoff_for_ready_report(
    semantic_project_factory, tmp_path, monkeypatch
):
    """End-to-end: ``catalog.readiness`` attaches a populated handoff.

    Exercises the real wiring: author an enriched metric, persist a fresh
    preview check, then call ``catalog.readiness``. The attached handoff's
    fingerprints must match the shared helpers computed from the same catalog
    index and workspace dir that the analysis-side validator checks.
    """
    from marivo.introspection.live.model import EnvironmentFingerprint, LiveHelpTarget

    catalog, snapshot = _ready_revenue_catalog_and_snapshot(
        semantic_project_factory, tmp_path, monkeypatch
    )
    revenue = catalog.get("metric.sales.revenue")
    catalog.preview(revenue.ref, using=snapshot, limit=2)
    report = catalog.readiness(refs=[revenue.ref])

    assert report.status in ("ready", "ready_with_warnings")
    assert report.analysis_handoff is not None
    h = report.analysis_handoff
    assert h.help_target == LiveHelpTarget(
        surface="analysis", canonical_id="boundary.semantic_handoff"
    )
    # The handoff carries only the requested/scoped ref, NOT the full
    # dependency closure. ``report.analysis_ready_refs`` certifies the whole
    # closure (metric + backing entity), but the handoff scopes to what the
    # caller asked for so the validator's readiness re-run is self-consistent.
    ready_ids = [str(r) for r in h.ready_refs]
    assert "sales.revenue" in ready_ids
    assert set(ready_ids) == {"sales.revenue"}
    assert "sales.revenue" in report.analysis_ready_refs
    assert "sales.orders" in report.analysis_ready_refs
    assert h.project_fingerprint == project_fingerprint(catalog.workspace_dir)
    index = catalog._require_index()
    assert h.catalog_fingerprint == catalog_fingerprint(obj.id for obj in index._by_id.values())
    assert h.environment_fingerprint == EnvironmentFingerprint.current()
    assert h.readiness_status == report.status
    assert h.warning_ids == tuple(sorted(w.kind for w in report.warnings))
    assert h.preview_evidence_ids == ()
    if report.status == "ready_with_warnings":
        assert h.caveats
    else:
        assert h.caveats == ()


def test_readiness_handoff_is_none_when_blocked(semantic_project_factory, tmp_path, monkeypatch):
    """A blocked readiness report carries no handoff.

    Request the metric ref WITHOUT persisting a preview check first, so
    readiness blocks on missing preview evidence (the catalog rejects raw
    string refs, so a real ref is required).
    """
    catalog, _ = _ready_revenue_catalog_and_snapshot(
        semantic_project_factory, tmp_path, monkeypatch
    )
    revenue = catalog.get("metric.sales.revenue")
    report = catalog.readiness(refs=[revenue.ref])
    assert report.status == "blocked"
    assert report.analysis_handoff is None


def test_attach_analysis_handoff_fingerprints_match_catalog(semantic_project_factory, tmp_path):
    """Direct producer test: fingerprints computed from the real catalog index.

    Builds a real catalog (no preview needed), constructs a ``ReadinessReport``
    with a known ready ref, and asserts ``_attach_analysis_handoff`` produces a
    handoff whose fingerprints exactly match the shared helpers run against the
    same index/workspace. This is the precision guarantee Task 1's shared
    helpers exist to provide.
    """
    from marivo.introspection.live.model import EnvironmentFingerprint
    from marivo.semantic.catalog import SemanticCatalog, SemanticKind
    from marivo.semantic.readiness import ReadinessInputSummary, ReadinessReport
    from marivo.semantic.refs import make_ref

    project = semantic_project_factory(
        {
            "sales/_domain.py": _READY_REVENUE_DOMAIN_PY,
            "sales/datasets.py": _READY_REVENUE_DATASETS_PY,
        }
    )
    catalog = SemanticCatalog(project)
    index = catalog._require_index()

    report = ReadinessReport(
        status="ready",
        analysis_ready_refs=("sales.revenue",),
        blockers=(),
        warnings=(),
        input_summary=ReadinessInputSummary(
            datasources=("warehouse",),
            refs=("sales.revenue",),
            tables=("sales.orders",),
        ),
        checked_at="2026-07-15T00:00:00Z",
    )

    from marivo.semantic.catalog import _attach_analysis_handoff

    attached = _attach_analysis_handoff(report, catalog)
    assert attached.analysis_handoff is not None
    h = attached.analysis_handoff
    assert h.ready_refs == (make_ref("sales.revenue", SemanticKind.METRIC),)
    assert h.project_fingerprint == project_fingerprint(catalog.workspace_dir)
    assert h.catalog_fingerprint == catalog_fingerprint(obj.id for obj in index._by_id.values())
    assert h.environment_fingerprint == EnvironmentFingerprint.current()
    assert h.readiness_status == "ready"
    assert h.warning_ids == ()
    assert h.preview_evidence_ids == ()
    assert h.caveats == ()


def test_attach_analysis_handoff_is_none_when_blocked(semantic_project_factory, tmp_path):
    """``_attach_analysis_handoff`` returns the report unchanged when blocked."""
    from marivo.semantic.catalog import SemanticCatalog, _attach_analysis_handoff
    from marivo.semantic.readiness import ReadinessInputSummary, ReadinessReport

    project = semantic_project_factory(
        {
            "sales/_domain.py": _READY_REVENUE_DOMAIN_PY,
            "sales/datasets.py": _READY_REVENUE_DATASETS_PY,
        }
    )
    catalog = SemanticCatalog(project)

    report = ReadinessReport(
        status="blocked",
        analysis_ready_refs=(),
        blockers=(),
        warnings=(),
        input_summary=ReadinessInputSummary(datasources=(), refs=(), tables=()),
        checked_at="2026-07-15T00:00:00Z",
    )
    attached = _attach_analysis_handoff(report, catalog)
    assert attached.analysis_handoff is None


def test_attach_analysis_handoff_caveats_for_ready_with_warnings(
    semantic_project_factory, tmp_path
):
    """A ``ready_with_warnings`` report yields a non-empty caveats tuple."""
    from marivo.semantic.catalog import SemanticCatalog, _attach_analysis_handoff
    from marivo.semantic.readiness import ReadinessInputSummary, ReadinessReport

    project = semantic_project_factory(
        {
            "sales/_domain.py": _READY_REVENUE_DOMAIN_PY,
            "sales/datasets.py": _READY_REVENUE_DATASETS_PY,
        }
    )
    catalog = SemanticCatalog(project)

    class _Warning:
        kind = "approximate_preview"

    report = ReadinessReport(
        status="ready_with_warnings",
        analysis_ready_refs=("sales.revenue",),
        blockers=(),
        warnings=(_Warning(),),
        input_summary=ReadinessInputSummary(
            datasources=("warehouse",),
            refs=("sales.revenue",),
            tables=("sales.orders",),
        ),
        checked_at="2026-07-15T00:00:00Z",
    )
    attached = _attach_analysis_handoff(report, catalog)
    assert attached.analysis_handoff is not None
    h = attached.analysis_handoff
    assert h.readiness_status == "ready_with_warnings"
    assert h.warning_ids == ("approximate_preview",)
    assert h.caveats == (
        "readiness includes non-blocking warnings; acceptance is a skill/user decision",
    )


def test_readiness_contract_analysis_handoff_targets_analysis_surface():
    from marivo.introspection.live.model import LiveHelpTarget
    from marivo.semantic._capabilities.contracts import contract_for_readiness_report

    contract = contract_for_readiness_report(
        analysis_ready_refs=("metric.sales.revenue",),
        blockers=(),
    )
    handoff_transitions = [t for t in contract.transitions if t.kind == "analysis_handoff"]
    assert handoff_transitions, "expected an analysis_handoff transition for the ready ref"
    for t in handoff_transitions:
        assert t.help_target == LiveHelpTarget(
            surface="analysis", canonical_id="boundary.semantic_handoff"
        )
        assert t.available is True


def test_readiness_contract_blocked_handoff_targets_analysis_surface():
    from marivo.introspection.live.model import LiveHelpTarget
    from marivo.semantic._capabilities.contracts import contract_for_readiness_report

    class _Blocker:
        refs = ("metric.sales.revenue",)
        kind = "snapshot_missing"

    contract = contract_for_readiness_report(
        analysis_ready_refs=(),
        blockers=(_Blocker(),),
    )
    blocked = [t for t in contract.transitions if t.kind == "analysis_handoff"]
    assert blocked
    for t in blocked:
        assert t.help_target == LiveHelpTarget(
            surface="analysis", canonical_id="boundary.semantic_handoff"
        )
        assert t.available is False


def _session_for_catalog(catalog, tmp_path):
    """Build an analysis Session over the given semantic catalog.

    The key invariant: ``session._project_root`` must equal
    ``catalog.workspace_dir`` (both resolve to ``tmp_path``) and
    ``session._catalog`` must be the same catalog instance the producer
    attached the handoff to. Delegates to the shared
    ``build_session_over_catalog`` helper used by the analysis session tests.
    """
    from tests.shared_fixtures import build_session_over_catalog

    return build_session_over_catalog(catalog, tmp_path)


def test_handoff_round_trip_through_analysis_validator(
    semantic_project_factory, tmp_path, monkeypatch
):
    """End-to-end: producer handoff validates through the analysis Session.

    Builds a ready catalog, runs readiness to attach the producer handoff, then
    hands it to ``Session.validate_semantic_handoff``. The returned receipt must
    mirror the handed-off facts.
    """
    from marivo.analysis._capabilities.model import SemanticHandoffReceipt

    catalog, snapshot = _ready_revenue_catalog_and_snapshot(
        semantic_project_factory, tmp_path, monkeypatch
    )
    revenue = catalog.get("metric.sales.revenue")
    catalog.preview(revenue.ref, using=snapshot, limit=2)
    report = catalog.readiness(refs=[revenue.ref])
    assert report.analysis_handoff is not None

    session = _session_for_catalog(catalog, tmp_path)
    receipt = session.validate_semantic_handoff(report.analysis_handoff)

    assert isinstance(receipt, SemanticHandoffReceipt)
    ready_ids = [str(r) for r in receipt.ready_refs]
    assert "sales.revenue" in ready_ids
    assert receipt.readiness_status == report.status
    assert receipt.warning_ids == report.analysis_handoff.warning_ids
    assert receipt.preview_evidence_ids == ()


def test_handoff_rejected_when_readiness_blocked(semantic_project_factory, tmp_path, monkeypatch):
    """A handoff whose refs are now blocked is rejected with semantic repair.

    Build a ready catalog + session + valid handoff, then monkeypatch the
    catalog's ``readiness`` so the validator's re-run returns a blocked report.
    Validation must raise ``AnalysisError`` with ``repair.kind == "semantic_handoff"``.
    """
    from marivo.analysis.errors import AnalysisError
    from marivo.semantic.readiness import ReadinessInputSummary, ReadinessReport

    catalog, snapshot = _ready_revenue_catalog_and_snapshot(
        semantic_project_factory, tmp_path, monkeypatch
    )
    revenue = catalog.get("metric.sales.revenue")
    catalog.preview(revenue.ref, using=snapshot, limit=2)
    ready_handoff = catalog.readiness(refs=[revenue.ref]).analysis_handoff
    assert ready_handoff is not None

    session = _session_for_catalog(catalog, tmp_path)

    blocked_report = ReadinessReport(
        status="blocked",
        analysis_ready_refs=(),
        blockers=(),
        warnings=(),
        input_summary=ReadinessInputSummary(datasources=(), refs=(), tables=()),
        checked_at="2026-07-15T00:00:00Z",
    )
    monkeypatch.setattr(catalog, "readiness", lambda **kw: blocked_report)

    with pytest.raises(AnalysisError) as exc_info:
        session.validate_semantic_handoff(ready_handoff)

    assert exc_info.value.repair is not None
    assert exc_info.value.repair.kind == "semantic_handoff"
    assert exc_info.value.repair.semantic_handoff is not None
    assert "blocked" in exc_info.value.message.lower()


def test_semantic_handoff_receipt_masks_environment_fingerprint():
    """The receipt's ordinary render masks env paths; repr is bounded.

    Per the three-layer fingerprint privacy rule, the receipt's ``to_dict``
    masks interpreter/package paths behind an opaque fingerprint id, and
    ``__repr__`` is a single bounded line (no full fingerprint dump).
    """
    from marivo.analysis._capabilities.model import (
        EnvironmentFingerprint,
        SemanticHandoffReceipt,
    )
    from marivo.refs import SemanticRef
    from marivo.semantic import SemanticKind

    fp = EnvironmentFingerprint.current()
    receipt = SemanticHandoffReceipt(
        ready_refs=(SemanticRef("sales.revenue", SemanticKind.METRIC),),
        project_fingerprint="proj-digest",
        catalog_fingerprint="cat-digest",
        environment_fingerprint=fp,
        readiness_status="ready",
        warning_ids=("missing_unit",),
    )

    rendered = receipt.to_dict()
    env_render = str(rendered["environment_fingerprint"])
    assert fp.python_executable not in env_render
    assert fp.package_path not in env_render
    assert "fingerprint" in env_render
    assert rendered["ready_refs"] == ["sales.revenue"]

    repr_text = repr(receipt)
    assert repr_text == "SemanticHandoffReceipt ready_refs=1 status=ready"
    assert "\n" not in repr_text
