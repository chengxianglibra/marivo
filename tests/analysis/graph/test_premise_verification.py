"""Exact invocation premises, complete pairing and source-owned checks."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.errors import AnalysisError
from marivo.datasource.adapters import CompiledRead, SourceSession
from tests.analysis.graph.physical_workloads import workload
from tests.shared_fixtures import DslCaseFactory


def _reads(monkeypatch: pytest.MonkeyPatch) -> list[CompiledRead]:
    reads: list[CompiledRead] = []
    execute = SourceSession.execute

    def read(source: SourceSession, issued: CompiledRead) -> pd.DataFrame:
        reads.append(issued)
        return execute(source, issued)

    monkeypatch.setattr(SourceSession, "execute", read)
    return reads


def test_observation_completeness_depends_on_exact_matching_assumptions(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as database:
        database.execute(
            "INSERT INTO order_line VALUES ('a1','j1_a',100),('a2','j1_a',200),('b1','j1_b',300),('c1','j1_c',400)"
        )
    session = case.session
    members = session.members(ms.ref.entity("sales.customer"))
    values = session.observe(
        {"value": session.catalog.metrics.get("sales.line_revenue")},
        during=session.time_scope(start="2026-08-01", end="2026-09-01"),
        by=members,
        members=members,
    )
    frame = values.execute().to_pandas()
    assert dict(zip(frame.iloc[:, 0], frame[("value", "value")], strict=True)) == {
        "A": 300,
        "B": 300,
        "C": 400,
        "D": 0,
    }
    assert values.rollup().execute().to_pandas()[("value", "value")].tolist() == [1000]
    route = session.catalog.relationships.get("sales.order_buyer")
    region = session.catalog.fields.get("sales.customer.region")
    orders = session.members(ms.ref.entity("sales.order"))
    inferred = session.read(orders, {"region": region}).execute().to_pandas()
    explicit = session.read(orders, {"region": region.via((route,))}).execute().to_pandas()
    assert inferred.equals(explicit)


def test_related_field_null_is_counted_and_missing_owner_is_rejected(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as database:
        database.execute("UPDATE customer SET region=NULL WHERE customer_id='B'")
    session = case.session
    orders = session.members(ms.ref.entity("sales.order"))
    region = session.catalog.fields.get("sales.customer.region")
    buyer = session.catalog.relationships.get("sales.order_buyer")
    values = session.read(orders, {"value": region.via((buyer,))})
    reads = _reads(monkeypatch)
    result = values.execute()
    frame = result.to_pandas()
    assert frame[("value", "value")].notna().any()
    assert frame[("value", "value")].isna().any()
    assert values.reduce({"count": mv.row_count()}).execute().to_pandas()[
        ("value", "count")
    ].tolist() == [len(frame)]
    assert reads
    with duckdb.connect(str(case.database_path)) as database:
        database.execute("DELETE FROM customer WHERE customer_id='A'")
    before = session.runs().items
    with pytest.raises(AnalysisError):
        values.where(values["value"] == "east").execute()
    assert session.runs().items != before
    assert session.artifact(result.id).to_pandas().equals(frame)


def test_declared_native_inputs_need_only_one_terminal_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with workload("duckdb", "ordinary-table", 64, "baseline", tmp_path, monkeypatch) as work:
        reads = _reads(monkeypatch)
        result = work.total().execute()
        assert work.validate(result)["passed"] is True
        assert sum("SUM(" in read.sql.upper() for read in reads) == 1
        before = len(reads)
        assert work.session.artifact(result.id).to_pandas().equals(result.to_pandas())
        assert len(reads) == before


def test_checked_pairing_checks_unknown_images_and_reuses_proven_images(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with workload("duckdb", "ordinary-table", 64, "baseline", tmp_path, monkeypatch) as work:
        session = work.session
        values = session.read(
            session.members(ms.ref.entity("cost.facts")),
            {"value": session.catalog.fields.get("cost.facts.y")},
        )
        left = values.where(values["value"] > 20)
        unequal = values.where(values["value"] > 16)
        matching = values.where(values["value"] > 20)
        before = session.runs().items
        paired = left.align(matching)
        assert session.runs().items == before
        with pytest.raises(AnalysisError, match="equal complete endpoint key images"):
            left.align(unequal).execute()
        ratios = paired.select({"value": paired["left.value"] / paired["right.value"]}).execute()
        expected = sum(isinstance(row["y"], int) and row["y"] > 20 for row in work.rows)
        assert ratios.to_pandas()[("value", "value")].tolist() == [1.0] * expected
        # Same-domain Columns share an input; independent Queries require actual exact pairing.
        shared = values.select({"value": values["value"] / values["value"]}).execute()
        assert shared.to_pandas()[("value", "value")].tolist() == [1.0] * 64
        assert session.artifact(ratios.id).to_pandas().equals(ratios.to_pandas())


def test_shared_matching_assumption_never_becomes_completed_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with workload("duckdb", "ordinary-table", 64, "baseline", tmp_path, monkeypatch) as work:
        session = work.session
        members = session.members(ms.ref.entity("cost.facts"))
        field = session.catalog.fields.get("cost.subjects.sid")
        relation = session.catalog.relationships.get("cost.facts_subject")
        values = session.read(members, {"value": field.via((relation,))})
        trusted = values.execute()
        frame = trusted.to_pandas()
        assert frame[("value", "value")].tolist() == [index % 15 for index in range(64)]
        selected = values.where(values["value"] == 1).execute()
        assert selected.to_pandas()[("value", "value")].tolist() == [1] * 5
        assert not hasattr(trusted, "_graph") and not hasattr(trusted, "_node")
        assert not hasattr(trusted, "rollup")
        assert session.artifact(trusted.id).to_pandas().equals(frame)


def test_exact_has_no_public_verification_choice() -> None:
    from inspect import signature

    assert tuple(signature(mv.Query.align).parameters) == ("self", "other", "match", "how")
    assert "verification" not in signature(mv.Query.compare).parameters


@pytest.mark.slow
def test_checked_pairing_survives_source_offline_cold_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json
    import os
    import subprocess
    import sys

    from tests.support.paths import PROJECT_ROOT

    with workload("duckdb", "ordinary-table", 64, "baseline", tmp_path, monkeypatch) as work:
        session = work.session
        values = session.read(
            session.members(ms.ref.entity("cost.facts")),
            {"value": session.catalog.fields.get("cost.facts.y")},
        )
        left, right = values.where(values["value"] > 20), values.where(values["value"] > 20)
        paired = left.align(right)
        saved = paired.select({"value": paired["left.value"] / paired["right.value"]}).execute()
        expected = len(saved.to_pandas())
        scalar = paired.reduce({"count": mv.row_count()}).execute()
        assert scalar.to_pandas()[("value", "count")].tolist() == [expected]
        (tmp_path / "models").rename(tmp_path / "models.offline")
        env = dict(os.environ, PYTHONPATH=str(PROJECT_ROOT))
        reports = []
        for _ in range(2):
            worker = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    """
import json, os, sys
import marivo.analysis as mv
import marivo.semantic as ms
from marivo.datasource.adapters import SourceSession

def forbidden(*args, **kwargs):
    raise AssertionError('terminal recovery touched a live source')
ms.load = forbidden
SourceSession.bind = forbidden
SourceSession.execute = forbidden
session = mv.session.resume(sys.argv[1], by='id')
before = len(session.runs().items)
saved = session.artifact(sys.argv[2])
frame = saved.to_pandas()
assert frame[('value','value')].tolist() == [1.0] * int(sys.argv[4])
assert frame[('value','value')].sum() == int(sys.argv[4])
assert session.artifact(sys.argv[3]).to_pandas()[('value','count')].tolist() == [int(sys.argv[4])]
assert len(session.runs().items) == before
print(json.dumps({'pid':os.getpid(),'rows':len(frame)}))
""",
                    session.id,
                    saved.id,
                    scalar.id,
                    str(expected),
                ],
                cwd=tmp_path,
                env=env,
                text=True,
                capture_output=True,
                timeout=60,
            )
            assert worker.returncode == 0, worker.stdout + worker.stderr
            reports.append(json.loads(worker.stdout.strip().splitlines()[-1]))
        assert len({os.getpid(), *(report["pid"] for report in reports)}) == 3
        assert all(report["rows"] == expected for report in reports)
