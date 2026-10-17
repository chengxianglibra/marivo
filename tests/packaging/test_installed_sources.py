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
BACKENDS = ("postgres", "mysql", "clickhouse", "trino")
NATIVE_OWNERS = (
    "tests/datasource/test_control_boundaries.py",
    "tests/datasource/test_acquisition_timeout.py",
    "tests/datasource/test_mysql_authoring_deadline.py",
    "tests/analysis/graph/test_method_consumers.py",
    "tests/analysis/materialization/test_producer_recovery.py",
    "tests/analysis/journey/test_native_funnel_recovery.py",
    "tests/analysis/numeric/test_native_numeric.py",
    "tests/datasource/test_source_profiles.py",
    "tests/analysis/graph/test_distribution_consumers.py",
    "tests/analysis/graph/test_multiroot_consumers.py",
)
METHOD_SELECTIONS = {
    "test_distribution_consumers.py": (
        "native_distribution and (int64 or "
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
        if engine == "mysql":
            phase("native")
    finally:
        if (project / "prefix").exists():
            phase("remove")
    phase("offline")
    produced = obj(read(candidate.reports / f"{engine}-produce.json")["result"])
    assert produced["query_status"] == "unqualified"
    assert produced["remote_artifact_cold_status"] == "unqualified"
    receipts = arr(produced["receipts"])
    assert len(receipts) == 4
    assert all(obj(item)["new_runs"] == 0 and obj(item)["new_artifacts"] == 0 for item in receipts)
    assert len(arr(produced["datasource_facts"])) == 4
    assert len(arr(produced["datasource_totals"])) == 2
    # Remote numerical/cold production has no qualified route. Local terminal
    # recovery is exercised independently by the installed DuckDB journeys.


@pytest.mark.parametrize(
    "owner,engine",
    [
        (owner, engine)
        for engine in ("postgres", "mysql", "trino", "clickhouse")
        for owner in NATIVE_OWNERS
        if not owner.endswith(("test_control_boundaries.py", "test_acquisition_timeout.py"))
        or engine in ("postgres", "trino", "clickhouse")
        if not owner.endswith(("test_mysql_authoring_deadline.py", "test_source_deadline.py"))
        or engine == "mysql"
        if not owner.endswith("test_method_consumers.py") or engine in ("mysql", "clickhouse")
    ],
)
def test_installed_native_behavior_owners(
    engine: str, owner: str, installed_multisource_wheel: InstalledWheel
) -> None:
    selected = os.environ.get("MARIVO_INSTALLED_BACKENDS", ",".join(BACKENDS)).split(",")
    if engine not in selected:
        pytest.skip("backend not selected for this serial service group")
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
            "-m",
            "runtime",
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
