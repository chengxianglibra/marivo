from __future__ import annotations

import ibis
import pytest

import marivo.analysis as mv
from marivo.analysis.frames.attribution import validate_generic_attribution_rows
from marivo.refs import SemanticKind
from tests.ref_helpers import make_ref
from tests.shared_fixtures import nonadditive_attribution_project_files


def test_count_distinct_overlap_reconciles_independent_endpoint(
    semantic_project_factory,
    monkeypatch,
) -> None:
    project = semantic_project_factory(nonadditive_attribution_project_files())
    monkeypatch.chdir(project.workspace_dir)
    backend = ibis.duckdb.connect(":memory:")
    backend.raw_sql(
        "CREATE TABLE orders (created_at DATE, region VARCHAR, channel VARCHAR, "
        "user_id INTEGER, amount DOUBLE)"
    )
    backend.raw_sql(
        "INSERT INTO orders VALUES "
        "(DATE '2026-01-01', 'US', 'web', 1, 10),"
        "(DATE '2026-01-02', 'CN', 'web', 1, 20),"
        "(DATE '2026-01-03', 'US', 'store', 2, 30),"
        "(DATE '2025-01-01', 'US', 'web', 1, 5),"
        "(DATE '2025-01-02', 'CN', 'store', 3, 15)"
    )
    session = mv.session.get_or_create(name="demo", backends={"warehouse": lambda: backend})
    metric = session.catalog.require(make_ref("sales.unique_users", SemanticKind.METRIC)).ref
    region = session.catalog.require(make_ref("sales.orders.region", SemanticKind.DIMENSION)).ref
    current = session.observe(
        metric, time_scope=mv.time_scope(start="2026-01-01", end="2026-02-01")
    )
    baseline = session.observe(
        metric, time_scope=mv.time_scope(start="2025-01-01", end="2025-02-01")
    )
    result = session.attribute(session.compare(current, baseline), axes=[region])

    rows = result.to_pandas().set_index("region")
    assert result.attribution_shape == "distinct_membership"
    assert rows.loc["US", "current_allocated_distinct"] == pytest.approx(1.5)
    assert rows.loc["CN", "current_allocated_distinct"] == pytest.approx(0.5)
    assert rows.loc["US", "contribution"] == pytest.approx(0.5)
    assert rows.loc["CN", "contribution"] == pytest.approx(-0.5)
    assert rows["contribution"].sum() == pytest.approx(0.0)
    assert result.meta.method_evidence is not None
    assert result.meta.method_evidence.kind == "distinct_membership"
    assert result.meta.method_evidence.overlap_key_count == 1
    assert result.meta.method_evidence.identities_persisted is False


def test_count_distinct_multiresolution_recomputes_each_prefix(
    semantic_project_factory,
    monkeypatch,
) -> None:
    project = semantic_project_factory(nonadditive_attribution_project_files())
    monkeypatch.chdir(project.workspace_dir)
    backend = ibis.duckdb.connect(":memory:")
    backend.raw_sql(
        "CREATE TABLE orders (created_at DATE, region VARCHAR, channel VARCHAR, "
        "user_id INTEGER, amount DOUBLE)"
    )
    backend.raw_sql(
        "INSERT INTO orders VALUES "
        "(DATE '2026-01-01', 'US', 'web', 1, 10),"
        "(DATE '2026-01-02', 'CN', 'web', 1, 20),"
        "(DATE '2026-01-03', 'US', 'store', 2, 30),"
        "(DATE '2025-01-01', 'US', 'web', 1, 5),"
        "(DATE '2025-01-02', 'CN', 'store', 3, 15)"
    )
    session = mv.session.get_or_create(name="demo", backends={"warehouse": lambda: backend})
    metric = session.catalog.require(make_ref("sales.unique_users", SemanticKind.METRIC)).ref
    region = session.catalog.require(make_ref("sales.orders.region", SemanticKind.DIMENSION)).ref
    channel = session.catalog.require(make_ref("sales.orders.channel", SemanticKind.DIMENSION)).ref
    current = session.observe(
        metric, time_scope=mv.time_scope(start="2026-01-01", end="2026-02-01")
    )
    baseline = session.observe(
        metric, time_scope=mv.time_scope(start="2025-01-01", end="2025-02-01")
    )
    result = session.attribute(
        session.compare(current, baseline),
        axes=[region, channel],
        mode="multiresolution",
    )

    rows = result.to_pandas()
    assert result.attribution_mode == "multiresolution"
    assert rows.groupby("attribution_level")["contribution"].sum().to_dict() == pytest.approx(
        {1: 0.0, 2: 0.0}
    )
    contract = result.contract()
    assert contract.row_arithmetic == "not_additive_across_resolutions"
    resolution_affordance = next(
        item
        for item in contract.affordances
        if item.capability_id == "AttributionFrame.at_resolution"
    )
    assert [item.semantic_refs for item in resolution_affordance.call_options] == [
        ("sales.orders.region",),
        ("sales.orders.region", "sales.orders.channel"),
    ]
    job_count = len(session.jobs())
    region_rows = result.at_resolution(axes=[region])
    assert len(session.jobs()) == job_count
    assert region_rows.contract().is_canonical is False
    assert region_rows.contract().row_arithmetic == "additive_once_per_comparison_bucket"
    assert not any(
        item.capability_id == "AttributionFrame.at_resolution"
        for item in region_rows.contract().affordances
    )
    assert set(region_rows.to_pandas()["attribution_level"]) == {1}
    assert region_rows.meta.method_evidence is not None
    assert region_rows.meta.method_evidence.multiresolution is not None
    assert region_rows.meta.method_evidence.multiresolution.scope.kind == "selected"

    corrupted = result.to_pandas()
    corrupted.loc[corrupted["attribution_level"] == 1, "contribution"] = 999.0
    with pytest.raises(ValueError, match="typed scope contribution sum"):
        validate_generic_attribution_rows(result.meta, corrupted)
    assert result.evidence_digest is not None
    assert all(
        item.rollup_safe is False
        and 1 <= len(item.resolution_axis_refs) <= 2
        and item.causal_claim == "none"
        for item in result.evidence_digest.items
        if item.kind == "contribution"
    )
    assert region_rows.evidence_digest is not None
    assert all(
        tuple(ref.path for ref in item.resolution_axis_refs) == ("sales.orders.region",)
        for item in region_rows.evidence_digest.items
        if item.kind == "contribution"
    )
    with pytest.raises(mv.errors.AttributionResolutionError):
        result.at_resolution(axes=[channel])


def test_count_distinct_excludes_null_keys_and_keeps_null_and_one_sided_axes(
    semantic_project_factory,
    monkeypatch,
) -> None:
    project = semantic_project_factory(nonadditive_attribution_project_files())
    monkeypatch.chdir(project.workspace_dir)
    backend = ibis.duckdb.connect(":memory:")
    backend.raw_sql(
        "CREATE TABLE orders (created_at DATE, region VARCHAR, channel VARCHAR, "
        "user_id INTEGER, amount DOUBLE)"
    )
    backend.raw_sql(
        "INSERT INTO orders VALUES "
        "(DATE '2026-01-01', 'US', 'web', NULL, 10),"
        "(DATE '2026-01-02', NULL, 'web', 1, 20),"
        "(DATE '2026-01-03', 'US', 'store', 2, 30),"
        "(DATE '2025-01-01', 'US', 'web', 2, 5),"
        "(DATE '2025-01-02', 'CN', 'store', 3, 15)"
    )
    session = mv.session.get_or_create(name="demo", backends={"warehouse": lambda: backend})
    metric = session.catalog.require(make_ref("sales.unique_users", SemanticKind.METRIC)).ref
    region = session.catalog.require(make_ref("sales.orders.region", SemanticKind.DIMENSION)).ref
    current = session.observe(
        metric, time_scope=mv.time_scope(start="2026-01-01", end="2026-02-01")
    )
    baseline = session.observe(
        metric, time_scope=mv.time_scope(start="2025-01-01", end="2025-02-01")
    )

    rows = session.attribute(session.compare(current, baseline), axes=[region]).to_pandas()

    null_axis = rows[rows["region"].isna()].iloc[0]
    assert null_axis["current_allocated_distinct"] == pytest.approx(1.0)
    assert null_axis["baseline_allocated_distinct"] == pytest.approx(0.0)
    assert rows.loc[rows["region"] == "CN", "contribution"].iloc[0] == pytest.approx(-1.0)
    assert rows["contribution"].sum() == pytest.approx(0.0)


def test_count_distinct_raw_keys_do_not_cross_the_artifact_boundary(
    semantic_project_factory,
    monkeypatch,
) -> None:
    project = semantic_project_factory(nonadditive_attribution_project_files())
    monkeypatch.chdir(project.workspace_dir)
    backend = ibis.duckdb.connect(":memory:")
    backend.raw_sql(
        "CREATE TABLE orders (created_at DATE, region VARCHAR, channel VARCHAR, "
        "user_id VARCHAR, amount DOUBLE)"
    )
    backend.raw_sql(
        "INSERT INTO orders VALUES "
        "(DATE '2026-01-01', 'US', 'web', 'private-key-alpha', 10),"
        "(DATE '2025-01-01', 'CN', 'web', 'private-key-beta', 5)"
    )
    session = mv.session.get_or_create(name="demo", backends={"warehouse": lambda: backend})
    metric = session.catalog.require(make_ref("sales.unique_users", SemanticKind.METRIC)).ref
    region = session.catalog.require(make_ref("sales.orders.region", SemanticKind.DIMENSION)).ref
    current = session.observe(
        metric, time_scope=mv.time_scope(start="2026-01-01", end="2026-02-01")
    )
    baseline = session.observe(
        metric, time_scope=mv.time_scope(start="2025-01-01", end="2025-02-01")
    )
    result = session.attribute(session.compare(current, baseline), axes=[region])

    assert "private-key" not in result.meta.model_dump_json()
    persisted = project.workspace_dir / ".marivo"
    for path in persisted.rglob("*"):
        if path.is_file():
            payload = path.read_bytes()
            assert b"private-key-alpha" not in payload
            assert b"private-key-beta" not in payload


def test_count_distinct_panel_source_reconciles_each_comparison_bucket(
    semantic_project_factory,
    monkeypatch,
) -> None:
    project = semantic_project_factory(nonadditive_attribution_project_files())
    monkeypatch.chdir(project.workspace_dir)
    backend = ibis.duckdb.connect(":memory:")
    backend.raw_sql(
        "CREATE TABLE orders (created_at DATE, region VARCHAR, channel VARCHAR, "
        "user_id INTEGER, amount DOUBLE)"
    )
    backend.raw_sql(
        "INSERT INTO orders VALUES "
        "(DATE '2026-01-01', 'US', 'web', 1, 10),"
        "(DATE '2026-01-02', 'CN', 'store', 1, 20),"
        "(DATE '2026-02-01', 'US', 'web', 2, 30),"
        "(DATE '2026-02-02', 'CN', 'store', 3, 40),"
        "(DATE '2025-01-01', 'US', 'web', 1, 5),"
        "(DATE '2025-02-01', 'CN', 'store', 2, 15)"
    )
    session = mv.session.get_or_create(name="demo", backends={"warehouse": lambda: backend})
    metric = session.catalog.require(make_ref("sales.unique_users", SemanticKind.METRIC)).ref
    region = session.catalog.require(make_ref("sales.orders.region", SemanticKind.DIMENSION)).ref
    channel = session.catalog.require(make_ref("sales.orders.channel", SemanticKind.DIMENSION)).ref
    current = session.observe(
        metric,
        time_scope=mv.time_scope(start="2026-01-01", end="2026-03-01"),
        grain=mv.grain("month"),
        dimensions=[channel],
    )
    baseline = session.observe(
        metric,
        time_scope=mv.time_scope(start="2025-01-01", end="2025-03-01"),
        grain=mv.grain("month"),
        dimensions=[channel],
    )

    result = session.attribute(session.compare(current, baseline), axes=[region])
    rows = result.to_pandas()

    assert current.meta.semantic_kind == "panel"
    assert rows.groupby("bucket_start")["contribution"].sum().tolist() == pytest.approx([0.0, 1.0])
    assert result.meta.bucket_column == "bucket_start"
