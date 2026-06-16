"""Tests for the deterministic replay.py static-check helper."""

from __future__ import annotations

from pathlib import Path

from marivo.analysis.publish import (
    ReplayCheckIssue,
    ReplayCheckResult,
    static_check_replay,
)

# A sales model with model id "sales" and metric "sales.revenue".
# Verified to load (status=ready, catalog metric ref "sales.revenue") via
# semantic_project_factory. The warehouse datasource file is required because the
# dataset uses md.ref('warehouse') (the factory only auto-creates datasources for
# quoted datasource='...' usages).
SALES_FILES = {
    "datasources/warehouse.py": (
        "import marivo.datasource as md\n"
        "warehouse = md.DatasourceSpec(name='warehouse', backend_type='duckdb', path=':memory:')\n"
        "md.datasource(warehouse)\n"
    ),
    "sales/__init__.py": "",
    "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales')\n",
    "sales/datasets.py": (
        "import marivo.semantic as ms\n"
        "import marivo.datasource as md\n"
        "\n"
        "warehouse = md.ref('warehouse')\n"
        "\n"
        "orders = ms.entity(name='orders', datasource=warehouse, source=ms.table('orders'))\n"
        "\n"
        "@ms.time_dimension(entity=orders, granularity='day', parse=ms.date())\n"
        "def order_date(orders):\n"
        "    return orders.created_at.cast('date')\n"
        "\n"
        "@ms.dimension(entity=orders)\n"
        "def region(orders):\n"
        "    return orders.region.upper()\n"
        "\n"
        "@ms.metric(entities=[orders], additivity='additive', "
        "name='revenue', )\n"
        "def revenue(orders):\n"
        "    return orders.amount.sum()\n"
    ),
}

# A replay script that passes every check; reused as the positive baseline.
GOOD_SCRIPT = (
    "import os\n"
    "import marivo.analysis as mv\n"
    "\n"
    'for _var in ("WAREHOUSE_DSN_ENV",):\n'
    "    if not os.environ.get(_var):\n"
    '        raise SystemExit(f"missing required datasource env var: {_var}")\n'
    "\n"
    'session = mv.session.get_or_create(name="replay")\n'
    "catalog = session.catalog\n"
    "cur = session.observe(\n"
    '    catalog.get("sales.revenue"),\n'
    '    timescope={"start": "2026-05-01", "end": "2026-05-08"},\n'
    ")\n"
    "print(cur.summary())\n"
)


def _workspace_dir(tmp_path: Path) -> Path:
    return tmp_path


def _write(tmp_path: Path, source: str) -> Path:
    script = tmp_path / "replay.py"
    script.write_text(source)
    return script


def test_good_script_passes_parse_and_load(tmp_path, semantic_project_factory):
    semantic_project_factory(SALES_FILES)
    result = static_check_replay(
        _write(tmp_path, GOOD_SCRIPT), workspace_dir=_workspace_dir(tmp_path)
    )
    assert isinstance(result, ReplayCheckResult)
    assert result.ok is True
    assert result.validation == "static_checked"
    assert result.issues == ()


def test_syntax_error_fails_with_parse_issue(tmp_path, semantic_project_factory):
    semantic_project_factory(SALES_FILES)
    result = static_check_replay(
        _write(tmp_path, "def (:\n"), workspace_dir=_workspace_dir(tmp_path)
    )
    assert result.ok is False
    assert result.validation == "failed"
    assert len(result.issues) == 1
    assert isinstance(result.issues[0], ReplayCheckIssue)
    assert result.issues[0].check == "parse"


def test_disallowed_import_is_flagged(tmp_path, semantic_project_factory):
    semantic_project_factory(SALES_FILES)
    bad = GOOD_SCRIPT.replace(
        "import marivo.analysis as mv\n",
        "import marivo.analysis as mv\nimport requests\n",
    )
    result = static_check_replay(_write(tmp_path, bad), workspace_dir=_workspace_dir(tmp_path))
    assert result.ok is False
    assert any(i.check == "imports" and "requests" in i.message for i in result.issues)


def test_unknown_session_intent_is_flagged(tmp_path, semantic_project_factory):
    semantic_project_factory(SALES_FILES)
    bad = GOOD_SCRIPT.replace("session.observe(", "session.observ(")
    result = static_check_replay(_write(tmp_path, bad), workspace_dir=_workspace_dir(tmp_path))
    assert result.ok is False
    assert any(i.check == "intent" and "observ" in i.message for i in result.issues)


def test_namespace_intents_are_allowed(tmp_path, semantic_project_factory):
    semantic_project_factory(SALES_FILES)
    ok_src = GOOD_SCRIPT + "anoms = session.discover.point_anomalies(cur, threshold=1.0)\n"
    result = static_check_replay(_write(tmp_path, ok_src), workspace_dir=_workspace_dir(tmp_path))
    assert not any(i.check == "intent" for i in result.issues)


def test_unresolved_metric_ref_is_flagged(tmp_path, semantic_project_factory):
    semantic_project_factory(SALES_FILES)
    bad = GOOD_SCRIPT.replace('"sales.revenue"', '"sales.unknown"')
    result = static_check_replay(_write(tmp_path, bad), workspace_dir=_workspace_dir(tmp_path))
    assert result.ok is False
    assert any(i.check == "metric_ref" and "sales.unknown" in i.message for i in result.issues)


def test_resolved_metric_ref_passes(tmp_path, semantic_project_factory):
    semantic_project_factory(SALES_FILES)
    result = static_check_replay(
        _write(tmp_path, GOOD_SCRIPT), workspace_dir=_workspace_dir(tmp_path)
    )
    assert not any(i.check == "metric_ref" for i in result.issues)


def test_inline_session_catalog_metric_ref_is_checked(tmp_path, semantic_project_factory):
    semantic_project_factory(SALES_FILES)
    src = GOOD_SCRIPT.replace(
        'catalog.get("sales.revenue")', 'session.catalog.get("sales.unknown")'
    )
    result = static_check_replay(_write(tmp_path, src), workspace_dir=_workspace_dir(tmp_path))
    assert any(i.check == "metric_ref" and "sales.unknown" in i.message for i in result.issues)


def test_variable_bound_catalog_metric_ref_is_checked(tmp_path, semantic_project_factory):
    semantic_project_factory(SALES_FILES)
    src = GOOD_SCRIPT.replace(
        'catalog.get("sales.revenue")',
        "unknown_metric",
    ).replace(
        "cur = session.observe(\n",
        'unknown_metric = catalog.get("sales.unknown")\ncur = session.observe(\n',
    )
    result = static_check_replay(_write(tmp_path, src), workspace_dir=_workspace_dir(tmp_path))
    assert any(i.check == "metric_ref" and "sales.unknown" in i.message for i in result.issues)


def test_variable_bound_catalog_metric_ref_passes(tmp_path, semantic_project_factory):
    semantic_project_factory(SALES_FILES)
    src = GOOD_SCRIPT.replace(
        'catalog.get("sales.revenue")',
        "revenue_metric",
    ).replace(
        "cur = session.observe(\n",
        'revenue_metric = catalog.get("sales.revenue")\ncur = session.observe(\n',
    )
    result = static_check_replay(_write(tmp_path, src), workspace_dir=_workspace_dir(tmp_path))
    assert not any(i.check == "metric_ref" for i in result.issues)


def test_later_catalog_ref_rebinding_does_not_affect_earlier_metric_use(
    tmp_path,
    semantic_project_factory,
):
    semantic_project_factory(SALES_FILES)
    src = GOOD_SCRIPT.replace(
        'catalog.get("sales.revenue")',
        "metric",
    ).replace(
        "cur = session.observe(\n",
        'metric = catalog.get("sales.revenue")\ncur = session.observe(\n',
    )
    src += 'metric = catalog.get("sales.unknown")\n'
    result = static_check_replay(_write(tmp_path, src), workspace_dir=_workspace_dir(tmp_path))
    assert not any(i.check == "metric_ref" for i in result.issues)


def test_non_catalog_get_is_not_checked_as_metric_ref(tmp_path, semantic_project_factory):
    semantic_project_factory(SALES_FILES)
    src = GOOD_SCRIPT + 'payload = {"sales.unknown": 1}\npayload.get("sales.unknown")\n'
    result = static_check_replay(_write(tmp_path, src), workspace_dir=_workspace_dir(tmp_path))
    assert result.ok is True
    assert not any(i.check == "metric_ref" for i in result.issues)


def test_dimension_catalog_get_is_not_checked_as_metric_ref(tmp_path, semantic_project_factory):
    semantic_project_factory(SALES_FILES)
    src = GOOD_SCRIPT + (
        'base = session.observe(catalog.get("sales.revenue"), '
        'timescope={"start": "2026-04-24", "end": "2026-05-01"})\n'
        "delta = session.compare(cur, base)\n"
        'axis = catalog.get("sales.orders.region").ref\n'
        "drivers = session.decompose(delta, axis=axis)\n"
    )
    result = static_check_replay(_write(tmp_path, src), workspace_dir=_workspace_dir(tmp_path))
    assert not any(i.check == "metric_ref" for i in result.issues)


def test_promote_metric_frame_source_arg_is_not_checked_as_metric_ref(
    tmp_path,
    semantic_project_factory,
):
    semantic_project_factory(SALES_FILES)
    src = GOOD_SCRIPT + (
        "scratch = session.from_pandas(cur.to_pandas())\n"
        'promoted = session.promote_metric_frame(scratch, metric=catalog.get("sales.revenue"))\n'
    )
    result = static_check_replay(_write(tmp_path, src), workspace_dir=_workspace_dir(tmp_path))
    assert not any(i.check == "metric_ref" for i in result.issues)


def test_undefined_frame_variable_is_flagged(tmp_path, semantic_project_factory):
    semantic_project_factory(SALES_FILES)
    bad = GOOD_SCRIPT + "delta = session.compare(ghost, cur)\n"
    result = static_check_replay(_write(tmp_path, bad), workspace_dir=_workspace_dir(tmp_path))
    assert result.ok is False
    assert any(i.check == "frame_var" and "ghost" in i.message for i in result.issues)


def test_defined_frame_variable_passes(tmp_path, semantic_project_factory):
    semantic_project_factory(SALES_FILES)
    ok_src = GOOD_SCRIPT + (
        "base = session.observe(\n"
        '    catalog.get("sales.revenue"),\n'
        '    timescope={"start": "2026-04-24", "end": "2026-05-01"},\n'
        ")\n"
        "delta = session.compare(cur, base)\n"
    )
    result = static_check_replay(_write(tmp_path, ok_src), workspace_dir=_workspace_dir(tmp_path))
    assert not any(i.check == "frame_var" for i in result.issues)


def test_relative_timescope_string_is_flagged(tmp_path, semantic_project_factory):
    semantic_project_factory(SALES_FILES)
    bad = GOOD_SCRIPT.replace(
        '    timescope={"start": "2026-05-01", "end": "2026-05-08"},\n',
        '    timescope="last_month",\n',
    )
    result = static_check_replay(_write(tmp_path, bad), workspace_dir=_workspace_dir(tmp_path))
    assert result.ok is False
    assert any(i.check == "timescope" for i in result.issues)


def test_timescope_missing_start_end_is_flagged(tmp_path, semantic_project_factory):
    semantic_project_factory(SALES_FILES)
    bad = GOOD_SCRIPT.replace(
        '    timescope={"start": "2026-05-01", "end": "2026-05-08"},\n',
        '    timescope={"grain": "day"},\n',
    )
    result = static_check_replay(_write(tmp_path, bad), workspace_dir=_workspace_dir(tmp_path))
    assert any(i.check == "timescope" for i in result.issues)


def test_hardcoded_secret_is_flagged(tmp_path, semantic_project_factory):
    semantic_project_factory(SALES_FILES)
    bad = GOOD_SCRIPT + 'aws_secret_access_key = "wJalrXUtnFEMI0K7MDENGbPxRfiCYEXAMPLEKEY"\n'
    result = static_check_replay(_write(tmp_path, bad), workspace_dir=_workspace_dir(tmp_path))
    assert result.ok is False
    assert any(i.check == "secret" for i in result.issues)


def test_env_lookup_is_not_a_secret(tmp_path, semantic_project_factory):
    semantic_project_factory(SALES_FILES)
    # GOOD_SCRIPT reads creds from os.environ and never embeds a value.
    result = static_check_replay(
        _write(tmp_path, GOOD_SCRIPT), workspace_dir=_workspace_dir(tmp_path)
    )
    assert not any(i.check == "secret" for i in result.issues)


def test_multiple_issues_are_reported_together(tmp_path, semantic_project_factory):
    semantic_project_factory(SALES_FILES)
    bad = (
        "import os\n"
        "import marivo.analysis as mv\n"
        "import requests\n"  # disallowed import
        'session = mv.session.get_or_create(name="replay")\n'
        "catalog = session.catalog\n"
        "cur = session.observe(\n"
        '    catalog.get("sales.unknown"),\n'  # unresolved metric
        '    timescope="last_month",\n'  # relative timescope
        ")\n"
        "delta = session.compare(ghost, cur)\n"  # undefined frame var
    )
    result = static_check_replay(_write(tmp_path, bad), workspace_dir=_workspace_dir(tmp_path))
    assert result.ok is False
    assert result.validation == "failed"
    checks = {i.check for i in result.issues}
    assert {"imports", "metric_ref", "timescope", "frame_var"} <= checks
