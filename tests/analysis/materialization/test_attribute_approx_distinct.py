"""Original approximate distinct uses native coalition reads and inert saved facts."""

import itertools
import json
import subprocess
import sys
from dataclasses import replace
from fractions import Fraction
from pathlib import Path

import duckdb
import ibis
import pandas as pd
import pyarrow.parquet as pq
import pytest

import marivo.analysis as mv
from marivo.analysis.compiler.graph_plan import read_reduction
from marivo.analysis.compiler.source_aggregates import admit_source_aggregate
from marivo.analysis.core.frame_method_facts import (
    FrameMethodParent,
    FrameShapleyMethod,
    FrameShapleyQueryGame,
    FrameShapleyReduction,
)
from marivo.analysis.errors import AnalysisError, AnalysisStageUnavailableError
from marivo.analysis.materialization.contracts import canonical_json
from marivo.analysis.materialization.errors import IntegrityError
from marivo.analysis.materialization.frame_method_facts import decode_method_payload
from marivo.analysis.materialization.graph_reading import open_manifest
from marivo.analysis.materialization.terminal_protocol import (
    decode_method_record,
    encode_method_record,
)
from marivo.analysis.session.core import Session
from tests.analysis.materialization.test_attribute_frequencies import _stage
from tests.analysis.materialization.test_frame_attribute_shapley import game_session as game_session
from tests.shared_fixtures import catalog_value
from tests.support.source_trace import capture_source


def _data(path: Path) -> None:
    with duckdb.connect(str(path)) as connection:
        connection.execute("DELETE FROM facts")
        connection.execute(
            "INSERT INTO facts VALUES "
            "(-1,'A',1,1,DATE '2026-07-01','east'),"
            "(1,'A',2,1,DATE '2026-07-01','east'),"
            "(2,'B',2,1,DATE '2026-07-01','east'),"
            "(0,NULL,NULL,1,DATE '2026-07-01','east'),"
            "(3,'A',3,1,DATE '2026-08-01','east'),"
            "(4,'B',4,1,DATE '2026-08-01','east'),"
            "(5,NULL,5,1,DATE '2026-08-01','east'),"
            "(6,'B',NULL,1,DATE '2026-08-01','east')"
        )


@pytest.mark.parametrize("filtered", [False, True], ids=["null_skip", "metric_where"])
def test_approximate_distinct_native_coalitions_preserve_where_nulls_cache_and_costs(
    game_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, filtered: bool
) -> None:
    database = tmp_path / "game.duckdb"
    _data(database)
    x = catalog_value(game_session.catalog, "sales.facts.x", kind="field")
    identifier = catalog_value(game_session.catalog, "sales.facts.id", kind="field")
    player = catalog_value(game_session.catalog, "sales.facts.player", kind="field")
    metric = x.approx_nunique().where(x >= 2) if filtered else x.approx_nunique()
    current = game_session.observe({"q": metric}, where=identifier >= 3)
    baseline = game_session.observe({"q": metric}, where=identifier < 3)
    compared = current.compare({"b": mv.baseline(baseline, on=())}).compute()
    target = compared.select({"q": compared["b.absolute.q"]})

    # Execute each original native reducer over selected rows, rather than
    # treating estimated per-player counts as mergeable state.
    players = (None, "A", "B")
    oracle = {}
    with duckdb.connect(str(database)) as connection:
        for mask in range(8):
            selection = " OR ".join(
                f"(player IS NOT DISTINCT FROM ? AND id {'>=' if mask & (1 << i) else '<'} 3)"
                for i in range(3)
            )
            predicate = "x >= 2" if filtered else "TRUE"
            oracle[mask] = connection.execute(
                f"SELECT approx_count_distinct(x) FROM facts WHERE {predicate} AND ({selection})",
                players,
            ).fetchone()[0]
    expected = [Fraction(0)] * 3
    for permutation in itertools.permutations(range(3)):
        before = 0
        for i in permutation:
            after = before | (1 << i)
            expected[i] += Fraction(oracle[after] - oracle[before], 6)
            before = after

    trace = capture_source(monkeypatch, measure_cost=True)
    report = target.attribute(by=(player,), permutations=256, seed=7).compute()
    rows = report.to_pandas()
    total = oracle[7] - oracle[0]
    assert rows["total.q"].tolist() == [total] * 3
    assert sum(rows["contribution.q"]) == pytest.approx(total)
    by_player = {
        None if pd.isna(key) else key: value
        for key, value in zip(rows.iloc[:, 0], rows["contribution.q"], strict=True)
    }
    assert set(by_player) == set(players)
    for i, key in enumerate(players):
        assert by_player[key] == pytest.approx(float(expected[i]), abs=0.15)
    method = report.method_facts[0].parents[0].metrics[0].method
    assert isinstance(method, FrameShapleyMethod)
    assert isinstance(method.game, FrameShapleyQueryGame)
    assert method.state_route == "native_queries/v1" and method.state_entries == 0
    assert method.game.metric_approximation == "native_approximate_metric/v1"
    assert method.game.standard_error == "observed_marginal_mean/v1"
    reduction = method.leaves[0].reduction
    assert isinstance(reduction, FrameShapleyReduction)
    assert reduction.reducer == "approx_nunique"
    assert method.unique_coalitions == method.coalition_evaluations == 8
    coalition_receipts = [c for c in method.captures if c.purpose == "coalition"]
    assert len(coalition_receipts) == 8
    assert len(trace.native_sql) == sum(c.source_reads for c in method.captures)
    assert sum(trace.returned_rows) == sum(c.returned_rows for c in method.captures)
    assert sum(trace.returned_arrow_bytes) == sum(c.returned_bytes for c in method.captures)
    sql = "\n".join(trace.native_sql).lower()
    assert "approx_count_distinct(" in sql
    assert "count(distinct" not in sql and "__attribute_frequency_value" not in sql

    saved = report.persist()
    code = """
import sys
from unittest.mock import patch
import marivo.analysis as mv
from marivo.analysis.session.core import Session
from marivo.datasource.adapters import SourceSession

def forbidden(*args, **kwargs):
    raise AssertionError('cold approximate distinct attempted source access')
with patch.object(Session, 'catalog', property(forbidden)), patch.object(SourceSession, '__init__', forbidden):
    frame = mv.session.resume(sys.argv[1]).persisted_frame(sys.argv[2])
    method = frame.method_facts[0].parents[0].metrics[0].method
    assert method.leaves[0].reduction.reducer == 'approx_nunique'
    assert method.game.metric_approximation == 'native_approximate_metric/v1'
    assert method.game.input_consistency == 'independent_reads/v1'
    assert method.unique_coalitions == 8 and method.state_entries == 0
    assert frame.to_pandas()['total.q'].tolist() == [int(sys.argv[3])]*3
"""
    process = subprocess.run(
        [sys.executable, "-c", code, saved.session_id, saved.artifact_id, str(total)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert process.returncode == 0, process.stdout + process.stderr
    descriptor = open_manifest(
        saved._local.store, saved.session_id, saved.artifact_id
    ).method_facts[0]
    payload = pq.read_table(
        tmp_path / descriptor.payload.rows.receipt.project_relative_path / "data.parquet"
    ).to_pandas()
    corrupt = payload.copy(deep=True)
    corrupt.loc[0, "record"] = corrupt.loc[0, "record"].replace('"seed":7', '"seed":8')
    with pytest.raises(IntegrityError, match="logical digest"):
        decode_method_payload(descriptor, corrupt)
    parent = report.method_facts[0].parents[0]
    wire = json.loads(encode_method_record(parent))
    wire["value"]["fields"]["metrics"][0]["fields"]["method"]["fields"]["game"]["fields"][
        "metric_approximation"
    ] = "exact_metric/v1"
    with pytest.raises(IntegrityError) as caught:
        decode_method_record(canonical_json(wire), FrameMethodParent)
    assert caught.value.received == "exact_metric/v1"
    wire = json.loads(encode_method_record(parent))
    wire["value"]["fields"]["metrics"][0]["fields"]["method"]["fields"]["leaves"][0]["fields"][
        "reduction"
    ]["fields"]["reducer"] = "approx_count_distinct"
    with pytest.raises(IntegrityError, match="exact type"):
        decode_method_record(canonical_json(wire), FrameMethodParent)


def test_approximate_distinct_strict_nulls_are_not_silently_ignored(
    game_session: Session, tmp_path: Path
) -> None:
    _data(tmp_path / "game.duckdb")
    x = catalog_value(game_session.catalog, "sales.facts.x", kind="field")
    identifier = catalog_value(game_session.catalog, "sales.facts.id", kind="field")
    player = catalog_value(game_session.catalog, "sales.facts.player", kind="field")
    metric = x.approx_nunique(nulls="error")
    current = game_session.observe({"q": metric}, where=identifier >= 3)
    baseline = game_session.observe({"q": metric}, where=identifier < 3)
    target = current.compare({"b": mv.baseline(baseline, on=())})
    with pytest.raises(AnalysisError, match="non-NA"):
        target.select({"q": target["b.absolute.q"]}).attribute(by=(player,)).compute()


@pytest.mark.parametrize("backend", ["postgres", "mysql"])
def test_unsupported_approximate_distinct_is_rejected_by_main_source_admission(
    backend: str,
) -> None:
    table = ibis.table({"category": "string", "amount": "int64"}, name="facts")
    prepared = read_reduction(_stage(table, backend, distinct=True))
    assert prepared is not None
    prepared = replace(prepared, node=replace(prepared.node, reducer="approx_nunique"))
    with pytest.raises(AnalysisStageUnavailableError, match=backend + ": approx_nunique"):
        admit_source_aggregate(prepared, {"sales.facts.amount": table.amount})
