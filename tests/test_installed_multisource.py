"""Opt-in wheel journeys against existing read-only multisource services."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

import pytest

pytestmark = [
    pytest.mark.release,
    pytest.mark.skipif(
        os.environ.get("MARIVO_INSTALLED_MULTISOURCE_TEST") != "1",
        reason="explicit installed multisource opt-in required",
    ),
]
ROOT = Path(__file__).resolve().parents[1]
BACKENDS = ("sqlite", "postgres", "mysql", "clickhouse", "clickhouse-cluster", "trino")
METHOD_CASES: dict[str, tuple[str, ...]] = {
    "sqlite": (
        "tests/test_lazy_sqlite_methods.py::test_entity_correlation",
        "tests/test_lazy_sqlite_methods.py::test_hidden_axis_attribution_matches_complete_source_sides",
        "tests/test_lazy_sqlite_methods.py::test_exact_distinct_membership",
        "tests/test_lazy_sqlite_methods.py::test_exact_distribution",
        "tests/test_lazy_sqlite_methods.py::test_status_fold_spatial_sums",
        "tests/test_lazy_sqlite_methods.py::test_remote_membership_rejects_duplicate_pair_even_with_matching_endpoint",
        "tests/test_lazy_sqlite_runtime.py::test_view_source_journey",
    ),
    "postgres": (
        "tests/test_lazy_postgres_methods.py::test_entity_correlation",
        "tests/test_lazy_postgres_methods.py::test_hidden_axis_attribution_complete_contributions",
        "tests/test_lazy_postgres_methods.py::test_exact_private_state",
        "tests/test_lazy_postgres_methods.py::test_status_fold_spatial_sums",
        "tests/test_lazy_postgres_forms_baseline.py::test_view_source_full_dataset_journey",
        "tests/test_lazy_postgres_event_methods.py::test_event_journey_is_one_read_only_source_submission",
        "tests/test_lazy_postgres_lifecycle_methods.py::test_complete_history_and_retained_parts",
        "tests/test_lazy_postgres_lifecycle_methods.py::test_source_reducers",
    ),
    "mysql": (
        "tests/test_lazy_mysql_methods.py::test_entity_correlation",
        "tests/test_lazy_mysql_methods.py::test_exact_private_state",
        "tests/test_lazy_mysql_methods.py::test_status_fold_spatial_sums",
        "tests/test_lazy_mysql_runtime.py::test_view_source_journey",
        "tests/test_lazy_computation_runtime.py::test_mysql_decimal_mean_stays_rejected_until_the_mean_equation_lands",
    ),
    "clickhouse": (
        "tests/test_lazy_clickhouse_methods.py::test_entity_correlation",
        "tests/test_lazy_clickhouse_methods.py::test_hidden_axis_attribution_complete_contributions[None]",
        "tests/test_lazy_clickhouse_methods.py::test_exact_distinct_membership",
        "tests/test_lazy_clickhouse_methods.py::test_exact_distribution",
        "tests/test_lazy_clickhouse_methods.py::test_status_fold_spatial_sums",
        "tests/test_lazy_clickhouse_runtime.py::test_view_source_journey",
        "tests/test_lazy_clickhouse_event_methods.py::test_event_journey_source_bundle",
        "tests/test_lazy_clickhouse_lifecycle_methods.py::test_complete_history",
    ),
    "clickhouse-cluster": (
        "tests/test_lazy_clickhouse_distributed_runtime.py::test_distributed_global_aggregation",
        "tests/test_lazy_clickhouse_distributed_runtime.py::test_cross_shard_duplicate_identity_rejected",
        "tests/test_lazy_clickhouse_distributed_runtime.py::test_receipt_audit_free_of_dedup_clauses",
        "tests/test_lazy_clickhouse_distributed_runtime.py::test_cluster_reader_account",
    ),
    "trino": (
        "tests/test_lazy_trino_methods.py::test_entity_correlation",
        "tests/test_lazy_trino_methods.py::test_hidden_axis_attribution_complete_contributions",
        "tests/test_lazy_trino_methods.py::test_hidden_axis_top_k_rejected_before_source_query",
        "tests/test_lazy_trino_methods.py::test_exact_private_state",
        "tests/test_lazy_trino_methods.py::test_status_fold_spatial_sums",
        "tests/test_lazy_trino_runtime.py::test_view_source_journey",
        "tests/test_lazy_trino_non_iceberg_runtime.py::test_full_journey",
        "tests/test_lazy_trino_non_iceberg_runtime.py::test_receipt_audit_free_of_iceberg_metadata",
        "tests/test_lazy_trino_non_iceberg_runtime.py::test_partitions_internal_table_rejected",
        "tests/test_lazy_trino_event_methods.py::test_exact_microsecond_journey",
        "tests/test_lazy_trino_lifecycle_methods.py::test_complete_history",
    ),
}
PARAMETERIZED_CASES = (
    "tests/test_lazy_scalar_type_runtime.py::test_scalar_group_transport_and_cold",
    "tests/test_lazy_temporal_backend_runtime.py::test_native_temporal_buckets",
    "tests/test_lazy_calendar_buckets.py::test_calendar_month_buckets_execute_on_source",
    "tests/test_lazy_cumulative_sources.py::test_grain_to_date_month_resets_across_months",
)
PARSED_CASES = (
    "tests/test_lazy_temporal_backend_runtime.py::test_strptime_date_only_axis_executes",
    "tests/test_lazy_temporal_backend_runtime.py::test_hour_prefix_composite_axis_executes",
)
DECIMAL_CASES = (
    "tests/test_lazy_computation_runtime.py::test_remote_computed_measure_decimal_sum_is_exact",
    "tests/test_lazy_computation_runtime.py::test_remote_decimal_linear_publishes_exact_decimal",
)
STAGE_ENV: dict[str, str] = {
    "postgres": "MARIVO_POSTGRES_ANALYSIS_TEST",
    "mysql": "MARIVO_MYSQL_ANALYSIS_TEST",
    "clickhouse": "MARIVO_CLICKHOUSE_ANALYSIS_TEST",
    "clickhouse-cluster": "MARIVO_CLICKHOUSE_CLUSTER_TEST",
    "trino": "MARIVO_TRINO_ANALYSIS_TEST",
}


@pytest.fixture(scope="module")
def installed_runner(tmp_path_factory: pytest.TempPathFactory) -> Callable[[str, Path], None]:
    wheels = tuple((ROOT / "dist/pypi").glob("marivo-*.whl"))
    assert len(wheels) == 1, "Run make pypi-build pypi-check first"
    wheel = wheels[0]
    work = tmp_path_factory.mktemp("multisource-wheel")
    assert not work.resolve().is_relative_to(ROOT)
    shutil.copytree(
        ROOT / "tests",
        work / "tests",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"),
    )
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("PYTHON", "PYTEST", "MARIVO_"))
    }
    env.update(
        PYTHONNOUSERSITE="1",
        MARIVO_TELEMETRY="off",
        MARIVO_WHEEL_SHA256=hashlib.sha256(wheel.read_bytes()).hexdigest(),
    )
    reports = Path(os.environ.get("MARIVO_MULTISOURCE_EVIDENCE_DIR", str(work / "reports")))
    reports = reports / uuid4().hex
    # A unique run directory prevents serial service groups overwriting receipts.
    reports.mkdir(parents=True, exist_ok=False)
    commands: list[dict[str, object]] = []

    def run(name: str, command: list[str], *, extra_env: dict[str, str] | None = None) -> None:
        completed = subprocess.run(
            command,
            cwd=work,
            env={**env, **(extra_env or {})},
            capture_output=True,
            text=True,
            timeout=1800,
            check=False,
        )
        (reports / f"{name}.log").write_text(completed.stdout + completed.stderr)
        commands.append(
            {"name": name, "command": command, "cwd": str(work), "exit_code": completed.returncode}
        )
        (reports / "commands.json").write_text(json.dumps(commands, indent=2) + "\n")
        assert completed.returncode == 0, completed.stdout + completed.stderr

    venv = work / ".venv"
    run("venv", [sys.executable, "-m", "venv", str(venv)])
    interpreter = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    constraints = work / "constraints.txt"
    constraints.write_text(
        "\n".join(
            sorted(
                f"{item.metadata['Name']}=={item.version}"
                for item in importlib.metadata.distributions()
                if item.metadata["Name"].lower() != "marivo"
            )
        )
        + "\n"
    )
    shutil.copy2(constraints, reports / "constraints.txt")
    run(
        "install",
        [
            str(interpreter),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--constraint",
            str(constraints),
            f"{wheel}[all]",
            "psycopg[binary]",
            "pytest",
        ],
    )

    def journey(engine: str, project: Path) -> None:
        probe = [str(interpreter), "-m", "tests.installed_multisource_probe"]

        def stage_tests() -> None:
            stage_env = {STAGE_ENV[engine]: "1"} if engine in STAGE_ENV else {}
            if engine == "trino":
                stage_env["MARIVO_TRINO_NON_ICEBERG_TEST"] = "1"
            stage_env["MARIVO_INSTALLED_ORIGIN_REPORT"] = str(
                reports / f"{engine}-stage-origin.json"
            )
            base = [
                str(interpreter),
                "-m",
                "pytest",
                "-q",
                "-m",
                "runtime",
                "-p",
                "tests.installed_wheel_probe",
            ]
            run(
                engine + "-stage-methods",
                [
                    *base,
                    f"--junitxml={reports / f'{engine}-stage-methods.xml'}",
                    *METHOD_CASES[engine],
                ],
                extra_env=stage_env,
            )
            if engine == "clickhouse-cluster":
                return
            run(
                engine + "-stage-parameterized",
                [
                    *base,
                    f"--junitxml={reports / f'{engine}-stage-parameterized.xml'}",
                    "-k",
                    engine,
                    *PARAMETERIZED_CASES,
                    *(PARSED_CASES if engine != "trino" else ()),
                    *(DECIMAL_CASES if engine in {"postgres", "mysql", "clickhouse"} else ()),
                ],
                extra_env=stage_env,
            )
            if engine == "trino":
                run(
                    engine + "-stage-computed",
                    [
                        *base,
                        f"--junitxml={reports / 'trino-stage-computed.xml'}",
                        "tests/test_lazy_computation_runtime.py::test_trino_computed_measure_decimal_sum_is_exact",
                        "tests/test_lazy_computation_runtime.py::test_trino_decimal_mean_keeps_rejection",
                    ],
                    extra_env=stage_env,
                )

        def phase(name: str) -> None:
            run(
                engine + "-" + name,
                [*probe, name, engine, str(project), str(reports / f"{engine}-{name}.json")],
            )

        if engine == "clickhouse-cluster":
            stage_tests()
            return
        try:
            phase("prepare")
            phase("privileges")
            phase("produce")
            if engine in {"sqlite", "mysql"}:
                phase("native")
            phase("invalidate")
            phase("invalid")
        finally:
            if (project / "prefix").exists():
                phase("remove")
        phase("cold")
        phase("offline")
        produced = json.loads((reports / f"{engine}-produce.json").read_text())
        cold = json.loads((reports / f"{engine}-cold.json").read_text())
        for receipt in cold["result"]["receipts"]:
            hit = receipt["binding_hit_statistics"]
            assert hit["statements"] == []
            assert hit["transferred_rows"] == hit["transferred_bytes"] == 0
            rollup = receipt["rollup_statistics"]
            assert rollup["primary_queries"] == rollup["transferred_rows"] == 1
            assert rollup["transferred_bytes"] > 0
            assert any(role == "primary" for role, _ in rollup["statements"])
        assert len(cold["result"]["receipts"]) == 3
        assert produced["pid"] != cold["pid"]
        assert produced["result"]["session"] == cold["result"]["session"]
        stage_tests()

    return journey


@pytest.mark.parametrize("engine", BACKENDS)
def test_installed_public_multisource_journey(
    engine: str, tmp_path: Path, installed_runner: Callable[[str, Path], None]
) -> None:
    selected = os.environ.get("MARIVO_INSTALLED_BACKENDS", ",".join(BACKENDS)).split(",")
    assert set(selected) <= set(BACKENDS), selected
    if engine not in selected:
        pytest.skip("backend not selected for this serial service group")
    installed_runner(engine, tmp_path / engine)
