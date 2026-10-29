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
from marivo.semantic.errors import SemanticExpressionError
from tests.analysis.graph.distribution_carrier_worker import check_original
from tests.analysis.materialization.domain_recovery_worker import snapshot
from tests.shared_fixtures import DslCaseFactory, catalog_value, terminal_runs
from tests.support.json import Json, checked, encode, obj, read
from tests.support.paths import PROJECT_ROOT
from tests.support.source_trace import capture_source


@pytest.mark.parametrize("physical", ["float", "decimal"])
@pytest.mark.slow
@pytest.mark.release
def test_named_distribution_carriers_recover_independently(
    analysis_dsl_case_factory: DslCaseFactory,
    physical: str,
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
        model.read_text().replace("parse=ms.timestamp(", "is_default=True, parse=ms.timestamp(", 1)
    )
    ms.load(workspace_dir=case.root)
    trace = capture_source(monkeypatch)
    session = case.session
    amount_field = catalog_value(session.catalog, "sales.order.amount", kind="field")
    time = catalog_value(session.catalog, "sales.order.ordered_at", kind="field")
    methods = {"quantile": amount_field.quantile(0.25, time=time)}
    if physical == "float":
        methods.update(
            distinct=amount_field.count_distinct(time=time),
            approx_distinct=amount_field.approx_nunique(time=time),
            median=amount_field.median(time=time),
            approx_median=amount_field.approx_median(time=time),
        )
        before = len(terminal_runs(session))
        with pytest.raises(SemanticExpressionError, match="qualified approximate quantile route"):
            session.observe(
                {"value": amount_field.approx_quantile(0.25, time=time)},
                by=session.members(session.catalog.get("entity:sales.customer")),
            ).persist()
        assert len(terminal_runs(session)) == before
    originals: dict[str, Json] = {}
    for name, metric in methods.items():
        value = session.observe(
            {"value": metric},
            by=mv.product(
                session.time_scope(start="2026-08-01", end="2026-09-01"),
                session.members(session.catalog.get("entity:sales.customer")),
            ),
        ).persist()
        exported = value.to_pandas()
        check_original(value, physical, name, exported=exported)
        originals[name] = {
            "artifact": value.artifact_id,
            "snapshot": snapshot(value, frame=exported),
        }
    state: dict[str, Json] = {
        "phase": "produce",
        "pid": os.getpid(),
        "session": case.session.id,
        "physical": physical,
        "form": "table",
        "originals": originals,
        "native_sql": [*trace.native_sql],
        "source_closed": all(owner._closed for owner in trace.owners),
    }
    assert trace.native_sql and trace.owners and state["source_closed"] is True
    (case.root / "r94-distribution-carrier.json").write_bytes(encode(state))
    shutil.rmtree(case.root / "models")
    case.database_path.unlink()
    reports: list[Json] = [state]
    repository = PROJECT_ROOT
    for phase in ("offline", "cold"):
        output = case.root / (phase + ".json")
        process = subprocess.run(
            [
                sys.executable,
                "-m",
                "tests.analysis.graph.distribution_carrier_worker",
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
        Path(directory, "distribution-carrier-" + physical + "-table.json").write_bytes(
            encode({"reports": checked(reports)})
        )
