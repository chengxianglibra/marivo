"""Registry-owned semantic-current admission for Artifact consumers."""

from __future__ import annotations

from pathlib import Path

import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis._capabilities.validation import validate_artifact_authority
from marivo.analysis.errors import ArtifactAuthorityUnknownError, ArtifactStaleError
from marivo.semantic.catalog import SemanticKind
from tests.ref_helpers import make_ref
from tests.shared_fixtures import connect_sales_orders, sales_backends


def _bootstrap_project(tmp_path: Path) -> None:
    (tmp_path / "marivo.toml").write_text('[project]\nname = "operator-admission"\n')
    datasource_dir = tmp_path / "models" / "datasources"
    semantic_dir = tmp_path / "models" / "semantic" / "sales"
    datasource_dir.mkdir(parents=True)
    semantic_dir.mkdir(parents=True)
    (datasource_dir / "warehouse.py").write_text(
        "import marivo.datasource as md\nmd.duckdb(name='warehouse', path=':memory:')\n"
    )
    (semantic_dir / "__init__.py").write_text("")
    (semantic_dir / "_domain.py").write_text(
        "import marivo.semantic as ms\nms.domain(name='sales', owner='Mina Zhang')\n"
    )
    (semantic_dir / "datasets.py").write_text(
        "import marivo.datasource as md\n"
        "import marivo.semantic as ms\n"
        "warehouse = ms.ref.datasource('warehouse')\n"
        "orders = ms.entity(name='orders', datasource=warehouse, source=md.table('orders'))\n"
        "@ms.time_dimension(entity=orders, granularity='day', is_default=True)\n"
        "def order_date(orders):\n"
        "    return orders.created_at.cast('date')\n"
        "@ms.metric(entities=[orders], additivity='additive', name='revenue')\n"
        "def revenue(orders):\n"
        "    return orders.amount.sum()\n"
        "@ms.metric(entities=[orders], additivity='additive', name='order_count')\n"
        "def order_count(orders):\n"
        "    return orders.order_id.count()\n"
    )


def _session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> mv.Session:
    monkeypatch.chdir(tmp_path)
    _bootstrap_project(tmp_path)
    return mv.session.get_or_create(
        name="operator-admission",
        backends=sales_backends(connect_sales_orders()),
        use_datasources=False,
    )


def _observe(
    session: mv.Session,
    *,
    start: str = "2026-07-01",
    end: str = "2026-07-31",
) -> mv.MetricFrame:
    return session.observe(
        metrics=make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope=mv.time_scope(start=start, end=end),
    )


def _semantic_file(tmp_path: Path) -> Path:
    return tmp_path / "models" / "semantic" / "sales" / "datasets.py"


def _reload_after_replacing(
    session: mv.Session,
    semantic_file: Path,
    old: str,
    new: str,
) -> None:
    semantic_file.write_text(semantic_file.read_text().replace(old, new))
    session._catalog = ms.load()


def test_semantic_current_operator_rejects_drift_with_real_authority_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(tmp_path, monkeypatch)
    frame = _observe(session)
    semantic_file = _semantic_file(tmp_path)
    _reload_after_replacing(session, semantic_file, "amount.sum()", "amount.mean()")

    with pytest.raises(ArtifactStaleError) as exc_info:
        session.discover.semantic_hypotheses(frame)

    error = exc_info.value
    assert error.kind == "artifact_stale"
    assert error.location == "session.discover.semantic_hypotheses.source"
    assert error._context["capability_id"] == "discover.semantic_hypotheses"
    assert error._context["parameter"] == "source"
    assert error._context["artifact_ref"] == (frame.meta.artifact_id or frame.meta.ref)
    assert error._context["definition_refs"] == ("metric:sales.revenue",)
    assert (
        error._context["recorded_catalog_fingerprint"]
        != error._context["current_catalog_fingerprint"]
    )
    assert len(str(error._context["authority_fingerprint"])) == 64
    assert "metric:sales.revenue" in (error.received or "")
    assert error.repair is not None
    assert "Re-run" in error.repair.action


def test_missing_dependency_authority_raises_unknown_before_operator_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(tmp_path, monkeypatch)
    frame = _observe(session)
    semantic_file = _semantic_file(tmp_path)
    original = semantic_file.read_text()
    semantic_file.write_text(original[: original.index("@ms.metric")])
    session._catalog = ms.load()

    with pytest.raises(ArtifactAuthorityUnknownError) as exc_info:
        session.discover.semantic_hypotheses(frame)

    error = exc_info.value
    assert error.kind == "artifact_authority_unknown"
    assert "metric:sales.revenue" in error._context["definition_refs"]
    assert (
        error._context["recorded_catalog_fingerprint"]
        != error._context["current_catalog_fingerprint"]
    )
    assert "metric:sales.revenue" in (error.received or "")
    assert error.repair is not None
    assert "Restore" in error.repair.action


def test_scoped_admission_ignores_unrelated_catalog_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(tmp_path, monkeypatch)
    frame = _observe(session)
    semantic_file = _semantic_file(tmp_path)
    _reload_after_replacing(
        session,
        semantic_file,
        "orders.order_id.count()",
        "orders.amount.count()",
    )

    validate_artifact_authority(
        "discover.semantic_hypotheses",
        "source",
        frame,
        session=session,
    )


def test_multi_source_closure_rejects_any_drift_before_attribution_business_checks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(tmp_path, monkeypatch)
    current = _observe(session, start="2026-07-16", end="2026-07-31")
    baseline = _observe(session, start="2026-07-01", end="2026-07-15")
    delta = session.compare(current, baseline)
    semantic_file = _semantic_file(tmp_path)
    _reload_after_replacing(session, semantic_file, "amount.sum()", "amount.mean()")

    with pytest.raises(ArtifactStaleError) as exc_info:
        session.attribute(delta, axes=[])

    context = exc_info.value._context
    assert context["capability_id"] == "attribute"
    assert context["parameter"] == "frame"
    assert set(context["source_refs"]) == {
        current.meta.artifact_id or current.meta.ref,
        baseline.meta.artifact_id or baseline.meta.ref,
    }
    assert context["definition_refs"] == ("metric:sales.revenue",)


def test_materialized_consumers_and_terminal_reads_remain_available_after_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(tmp_path, monkeypatch)
    current = _observe(session, start="2026-07-16", end="2026-07-31")
    baseline = _observe(session, start="2026-07-01", end="2026-07-15")
    semantic_file = _semantic_file(tmp_path)
    _reload_after_replacing(session, semantic_file, "amount.sum()", "amount.mean()")

    delta = session.compare(current, baseline)
    transformed = current.transform.topk(by=current.value_columns[0], limit=1)

    assert not delta.to_pandas().empty
    assert len(transformed.to_pandas()) == 1


def test_authority_validator_does_not_touch_datasource_and_contract_is_static(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(tmp_path, monkeypatch)
    frame = _observe(session)

    class _ExplodingRuntime:
        def __getattribute__(self, name: str) -> object:
            raise AssertionError(f"datasource runtime accessed: {name}")

    def _unexpected_revalidation(*args: object, **kwargs: object) -> object:
        raise AssertionError("operator admission called session.revalidate()")

    monkeypatch.setattr(type(session), "revalidate", _unexpected_revalidation)
    runtime = session._connection_runtime
    session._connection_runtime = _ExplodingRuntime()  # type: ignore[assignment]
    try:
        validate_artifact_authority(
            "discover.semantic_hypotheses",
            "source",
            frame,
            session=session,
        )
    finally:
        session._connection_runtime = runtime

    semantic_file = _semantic_file(tmp_path)
    _reload_after_replacing(session, semantic_file, "amount.sum()", "amount.mean()")

    def _unexpected_currentness(*args: object, **kwargs: object) -> object:
        raise AssertionError("contract() performed currentness evaluation")

    monkeypatch.setattr(
        "marivo.analysis._artifact_authority.evaluate_semantic_authority",
        _unexpected_currentness,
    )
    assert frame.contract().ref == frame.meta.ref
