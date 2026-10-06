"""Bind the existing DOUBLE and Decimal distribution producers to full recovery."""

import os
import shutil
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.semantic.ir import AggKind
from tests.json_support import Json, checked, encode, obj, read
from tests.r93_source_trace import capture_source
from tests.r94_attribution_carrier_worker import parts
from tests.r94_distribution_carrier_worker import check_original
from tests.r94_domain_recovery_worker import snapshot
from tests.shared_fixtures import DslCaseFactory, export_dsl_parquet_models


@pytest.mark.runtime
@pytest.mark.parametrize(
    "physical,parquet", [("float", False), ("float", True), ("decimal", False)]
)
def test_named_distribution_carriers_recover_independently(
    analysis_dsl_case_factory: DslCaseFactory,
    physical: str,
    parquet: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as db:
        db.execute(
            'ALTER TABLE "order" ALTER amount TYPE '
            + ("DOUBLE" if physical == "float" else "DECIMAL(18,2)")
        )
        if physical == "decimal":
            db.execute('DELETE FROM "order"')
            for index, (member, amount) in enumerate(
                (member, amount)
                for member, amount in (
                    ("A", Decimal("1.01")),
                    ("B", Decimal("1.02")),
                    ("C", Decimal("1.04")),
                )
                for _ in range(2)
            ):
                db.execute(
                    'INSERT INTO "order" VALUES (?, ?, ?, ?, ?, ?)',
                    [str(index), member, "web", "paid", "2026-08-15", amount],
                )
    model = case.root / "models/semantic/sales/models.py"
    model.write_text(
        model.read_text().replace("granularity='second',", "granularity='second', is_default=True,")
    )
    if parquet:
        export_dsl_parquet_models(case, case.root)
    ms.load(workspace_dir=case.root)
    trace = capture_source(monkeypatch)
    methods: dict[str, AggKind] = {"quantile": ("percentile", 0.25)}
    if physical == "float":
        methods = {
            "distinct": "count_distinct",
            "approx_distinct": "approx_count_distinct",
            "median": "median",
            "approx_median": "approx_median",
            "quantile": ("percentile", 0.25),
            "approx_quantile": ("approx_percentile", 0.25),
        }
    originals: dict[str, Json] = {}
    original_parts: dict[str, Json] = {}
    for name, agg in methods.items():
        metric = mv.runtime_metric.aggregate(
            ms.ref.measure("sales.order.amount"), agg=agg, label=name
        )
        value = (
            case.session.members(ms.ref.entity("sales.customer"))
            .observe(
                metric,
                during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
                via=ms.ref.relationship("sales.order_buyer"),
            )
            .execute()
        )
        assert isinstance(value, mv.MaterializedNumericRelation)
        check_original(value, physical, name)
        originals[name], original_parts[name] = snapshot(value), parts(value)
    state: dict[str, Json] = {
        "phase": "produce",
        "pid": os.getpid(),
        "session": case.session.id,
        "physical": physical,
        "form": "parquet" if parquet else "table",
        "originals": originals,
        "parts": original_parts,
        "native_sql": [*trace.native_sql],
        "source_closed": all(owner._closed for owner in trace.owners),
    }
    assert trace.native_sql and trace.owners and state["source_closed"] is True
    (case.root / "r94-distribution-carrier.json").write_bytes(encode(state))
    shutil.rmtree(case.root / "models")
    case.database_path.unlink()
    if (case.root / "source_files").exists():
        shutil.rmtree(case.root / "source_files")
    reports: list[Json] = [state]
    repository = Path(__file__).resolve().parents[1]
    for phase in ("fixed", "cold"):
        output = case.root / (phase + ".json")
        process = subprocess.run(
            [
                sys.executable,
                "-m",
                "tests.r94_distribution_carrier_worker",
                str(case.root),
                phase,
                str(output),
            ],
            cwd=repository,
            env={**os.environ, "PYTHONPATH": str(repository)},
            capture_output=True,
            text=True,
            timeout=180,
        )
        assert process.returncode == 0, process.stdout + process.stderr
        reports.append(read(output))
    assert len({str(obj(report)["pid"]) for report in reports}) == 3
    if directory := os.environ.get("MARIVO_R93_EVIDENCE_DIR"):
        Path(
            directory, "distribution-carrier-" + physical + "-" + str(state["form"]) + ".json"
        ).write_bytes(encode({"reports": checked(reports)}))
