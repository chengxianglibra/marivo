"""Opt-in installed public journeys against existing read-only sources."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.packaging.wheel_support import InstalledWheel
from tests.support.json import arr, obj, read

pytestmark = [
    pytest.mark.release,
    pytest.mark.skipif(
        os.environ.get("MARIVO_INSTALLED_MULTISOURCE_TEST") != "1",
        reason="explicit installed multisource opt-in required",
    ),
]
BACKENDS = ("sqlite", "postgres", "mysql", "clickhouse", "trino")
NATIVE_OWNERS = (
    "tests/analysis/materialization/test_native_graph_deadline.py",
    "tests/datasource/test_mysql_authoring_deadline.py",
    "tests/datasource/test_source_deadline.py",
    "tests/analysis/graph/test_method_consumers.py",
    "tests/analysis/materialization/test_producer_recovery.py",
    "tests/analysis/graph/test_remote_domain_consumers.py",
    "tests/analysis/journey/test_native_funnel_recovery.py",
    "tests/analysis/numeric/test_native_numeric.py",
    "tests/datasource/test_source_profiles.py",
    "tests/analysis/materialization/test_graph_transport_phases.py",
    "tests/analysis/graph/test_reference_consumers.py",
    "tests/analysis/graph/test_distribution_consumers.py",
    "tests/analysis/graph/test_multiroot_consumers.py",
    "tests/analysis/numeric/test_comparison_consumers.py",
    "tests/analysis/materialization/test_attribution_recovery.py",
)
METHOD_SELECTIONS = {
    "test_distribution_consumers.py": (
        "native_distribution and (int64 or "
        "(sqlite and (timestamp or string)) or "
        "(postgres and (distinct-decimal or quantile-float64)) or "
        "(mysql and distinct-decimal) or (trino and approx_quantile-float64) or "
        "(clickhouse and (approx_distinct-boolean or approx_quantile-float64)))"
    ),
    "test_multiroot_consumers.py": (
        "independent_roots and (linear or ratio or first) and not "
        "(decimal or nonfinite or overflow or fanout or composite or float or spatial)"
    ),
}


@pytest.mark.parametrize("engine", BACKENDS)
def test_installed_public_multisource_journey(
    engine: str, tmp_path: Path, installed_multisource_wheel: InstalledWheel
) -> None:
    selected = os.environ.get("MARIVO_INSTALLED_BACKENDS", ",".join(BACKENDS)).split(",")
    assert set(selected) <= set(BACKENDS), selected
    if engine not in selected:
        pytest.skip("backend not selected for this serial service group")
    candidate = installed_multisource_wheel
    project = tmp_path / engine

    def phase(name: str) -> None:
        candidate.run(
            engine + "-" + name,
            [
                str(candidate.interpreter),
                "-m",
                "tests.packaging.source_probe",
                name,
                engine,
                str(project),
                str(candidate.reports / f"{engine}-{name}.json"),
            ],
        )

    try:
        for name in ("prepare", "privileges", "produce"):
            phase(name)
        if engine in {"sqlite", "mysql"}:
            phase("native")
        phase("invalidate")
        phase("invalid")
    finally:
        if (project / "prefix").exists():
            phase("remove")
    phase("fixed")
    phase("cold")
    phase("offline")
    produced = read(candidate.reports / f"{engine}-produce.json")
    cold = read(candidate.reports / f"{engine}-cold.json")
    fixed = read(candidate.reports / f"{engine}-fixed.json")
    result = obj(cold["result"])
    assert result["source_tables_removed"] is True
    assert result["source_connection_forbidden"] is True
    receipts = arr(result["receipts"])
    for item in receipts:
        receipt = obj(item)
        assert receipt["new_runs"] == 0
        assert receipt["cache_reused"] is True
    assert len(receipts) == 3
    assert len({produced["pid"], fixed["pid"], cold["pid"]}) == 3
    assert all(obj(receipt)["new_runs"] == 1 for receipt in arr(obj(fixed["result"])["receipts"]))
    assert obj(produced["result"])["session"] == obj(cold["result"])["session"]


@pytest.mark.parametrize("engine", ("postgres", "mysql", "trino", "clickhouse"))
@pytest.mark.parametrize("owner", NATIVE_OWNERS)
def test_installed_native_behavior_owners(
    engine: str, owner: str, installed_multisource_wheel: InstalledWheel
) -> None:
    selected = os.environ.get("MARIVO_INSTALLED_BACKENDS", ",".join(BACKENDS)).split(",")
    if engine not in selected:
        pytest.skip("backend not selected for this serial service group")
    if owner.endswith("test_graph_transport_phases.py") and engine not in ("mysql", "trino"):
        pytest.skip("transport phase owner applies only to MySQL and Trino")
    if (
        owner.endswith(("test_mysql_authoring_deadline.py", "test_source_deadline.py"))
        and engine != "mysql"
    ):
        pytest.skip("affected MySQL reader cancellation owner")
    candidate = installed_multisource_wheel
    opt_ins = {
        name: value
        for name, value in os.environ.items()
        if name
        in (
            "MARIVO_POSTGRES_ANALYSIS_TEST",
            "MARIVO_MYSQL_ANALYSIS_TEST",
            "MARIVO_TRINO_ANALYSIS_TEST",
            "MARIVO_TRINO_NON_ICEBERG_TEST",
            "MARIVO_CLICKHOUSE_ANALYSIS_TEST",
            "MARIVO_CLICKHOUSE_CLUSTER_TEST",
        )
    }
    name = engine + "-" + owner.rsplit("/", 1)[-1].removesuffix(".py")
    evidence = candidate.reports / name
    evidence.mkdir()
    projects = candidate.work / "owner_projects"
    projects.mkdir(exist_ok=True)
    expression = engine + (" or non_iceberg" if engine == "trino" else "")
    if selection := METHOD_SELECTIONS.get(owner.rsplit("/", 1)[-1]):
        expression = f"({expression}) and ({selection})"
    candidate.run(
        name,
        [
            str(candidate.interpreter),
            "-m",
            "pytest",
            "-q",
            "--tb=short",
            "--maxfail=5",
            "--basetemp=" + str(projects / name),
            "-k",
            expression,
            owner,
        ],
        extra_env={
            **opt_ins,
            "MARIVO_R93_EVIDENCE_DIR": str(evidence),
            "MARIVO_R92_EVIDENCE_DIR": str(evidence),
            "MARIVO_R94_DOMAIN_RECOVERY": "1",
        },
    )
