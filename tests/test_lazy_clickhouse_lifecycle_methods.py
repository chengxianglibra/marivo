"""Read-only ClickHouse complete Lifecycle history qualification."""

from __future__ import annotations

import os
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

from marivo.analysis.materialization.admission import DatasetRuntime
from tests.lazy_clickhouse_event_fixtures import event_registry, event_source_tables
from tests.lazy_lifecycle_fixtures import START, history, lifecycle_registry
from tests.lazy_remote_lifecycle_fixtures import assert_cold_history, assert_complete_history
from tests.multisource_environment import clickhouse_analysis as ch

pytestmark = [
    pytest.mark.runtime,
    pytest.mark.skipif(
        os.environ.get("MARIVO_CLICKHOUSE_ANALYSIS_TEST") != "1", reason="opt-in ClickHouse service"
    ),
]


def test_complete_history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with event_source_tables() as names:
        with ch.connection(admin=True) as admin:
            for key in ("started_rows", "finished_rows"):
                admin.command(f"TRUNCATE TABLE {names[key]}")
            admin.insert(
                names["started_rows"],
                [(11, 1, START - timedelta(hours=1)), (21, 2, START + timedelta(hours=2))],
            )
            admin.insert(
                names["finished_rows"],
                [(12, 1, START + timedelta(hours=3)), (13, 1, START + timedelta(hours=4))],
            )
        base, sidecar = event_registry(names, monkeypatch)
        model, model_sidecar = lifecycle_registry(Path("unused.duckdb"))
        registry = replace(base, state_models=model.state_models)
        registry.freeze()
        sidecar = replace(sidecar, catalog_refs=model_sidecar.catalog_refs)
        runtime = DatasetRuntime.create(tmp_path / "history", "clickhouse-lifecycle")
        result = history(runtime.sources(semantic_registry=registry, sidecar=sidecar)).execute()
        assert_complete_history(runtime, result)
        submissions = [
            item for item in runtime.statistics.submissions if item.role == "lifecycle_bundle"
        ]
        assert len(submissions) == 1
        with ch.connection() as reader:
            assert reader.query("SELECT currentUser(),getSetting('readonly')").result_rows == [
                ("analysis_reader", 1)
            ]
        with ch.connection(admin=True) as admin:
            admin.command("SYSTEM FLUSH LOGS")
            observed = admin.query(
                "SELECT query,user FROM system.query_log WHERE type='QueryFinish' AND user='analysis_reader' AND startsWith(query,'WITH ') AND position(query,{table:String})>0",
                parameters={"table": names["started_rows"]},
            ).result_rows
        assert len(observed) == 1
        assert observed[0][1] == "analysis_reader"
        assert observed[0][0].removesuffix("\n FORMAT Native") == submissions[0].sql
    assert_cold_history(tmp_path / "history", runtime.session_ref, result.state.artifact_ref.ref)


@pytest.mark.parametrize(
    "case",
    [
        "long",
        "ambiguous",
        "outcome_ambiguous",
        "compatible",
        "empty",
        "empty_duplicate",
        "duplicate",
        "null",
        "null_time",
        "missing_participant",
        "no_inception",
        "unknown",
        "illegal",
        "microsecond",
        "stream_failure",
        "part_failure",
    ],
)
def test_replay_boundaries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str) -> None:
    import sqlite3
    from collections.abc import Iterator

    import pyarrow as pa

    from marivo.analysis.materialization.errors import MaterializationError
    from marivo.analysis.materialization.lifecycle_bundle import LifecyclePartStream

    with event_source_tables() as names:
        with ch.connection(admin=True) as admin:
            for key in ("started_rows", "finished_rows"):
                admin.command(f"TRUNCATE TABLE {names[key]}")
            if case == "empty_duplicate":
                admin.insert(names["customers"], [(1, "EU")])
            elif case not in ("empty", "no_inception"):
                admin.insert(
                    names["started_rows"],
                    [
                        (
                            None if case == "null" else 1,
                            None if case == "missing_participant" else 1,
                            None if case == "null_time" else START,
                        )
                    ],
                )
            if case in ("duplicate", "illegal", "outcome_ambiguous"):
                admin.insert(
                    names["started_rows"],
                    [(1 if case == "duplicate" else 3, 1, START + timedelta(hours=1))],
                )
            if case in (
                "ambiguous",
                "outcome_ambiguous",
                "compatible",
                "no_inception",
                "unknown",
                "long",
                "microsecond",
            ):
                instant = (
                    START
                    if case == "ambiguous"
                    else START + timedelta(microseconds=2)
                    if case == "microsecond"
                    else START + timedelta(hours=1)
                )
                admin.insert(names["finished_rows"], [(2, 1, instant)])
            if case == "compatible":
                for key, identity in (("started_rows", 3), ("finished_rows", 4)):
                    admin.insert(names[key], [(identity, 1, START + timedelta(hours=2))])
            if case == "long":
                # One same-Event tie larger than Trino's former recursion cap.
                admin.insert(
                    names["finished_rows"],
                    [(100 + i, 1, START + timedelta(hours=2, microseconds=1)) for i in range(24)],
                )
        base, sidecar = event_registry(names, monkeypatch)
        model, model_sidecar = lifecycle_registry(Path("unused.duckdb"))
        registry = replace(base, state_models=model.state_models)
        registry.freeze()
        sidecar = replace(sidecar, catalog_refs=model_sidecar.catalog_refs)
        runtime = DatasetRuntime.create(tmp_path / "boundary", "clickhouse-lifecycle")
        logical = history(
            runtime.sources(semantic_registry=registry, sidecar=sidecar), complete=case != "unknown"
        )
        if case in ("stream_failure", "part_failure"):
            original = LifecyclePartStream.__iter__

            def broken(stream: LifecyclePartStream) -> Iterator[pa.RecordBatch]:
                for batch in original(stream):
                    yield batch
                    if stream.index == (0 if case == "stream_failure" else 2):
                        raise RuntimeError("injected late Lifecycle failure")

            monkeypatch.setattr(LifecyclePartStream, "__iter__", broken)
        if case in (
            "ambiguous",
            "outcome_ambiguous",
            "empty_duplicate",
            "duplicate",
            "null",
            "null_time",
            "missing_participant",
            "no_inception",
            "stream_failure",
            "part_failure",
        ):
            expected = (
                RuntimeError if case in ("stream_failure", "part_failure") else MaterializationError
            )
            with pytest.raises(expected, match=r"injected late|validation failed"):
                logical.execute()
            with sqlite3.connect(runtime.store.db_path) as local:
                assert local.execute("SELECT count(*) FROM dataset_artifacts").fetchone() == (0,)
        else:
            result = logical.execute()
            frame = result.to_pandas()
            assert frame.model_state.tolist() == (
                [] if case == "empty" else ["open"] if case == "illegal" else ["open", "done"]
            )
            if case == "unknown":
                assert frame.interval_status.tolist() == ["coverage_censored", "coverage_censored"]
            if case in ("long", "compatible", "illegal"):
                violations = result.violations().execute().to_pandas()
                assert len(violations) == (
                    24 if case == "long" else 2 if case == "compatible" else 1
                )
            if case == "microsecond":
                assert frame.valid_to.iloc[0] - frame.valid_from.iloc[0] == timedelta(
                    microseconds=2
                )


def test_native_governed_tie_order() -> None:
    from marivo.analysis.compiler.lifecycle_array import clickhouse_confluence, clickhouse_replay
    from marivo.analysis.domains.lifecycle import LifecycleSemantics
    from tests.lazy_lifecycle_fixtures import sources_without_io

    semantics = history(sources_without_io()).row_contract.family_semantics
    assert isinstance(semantics, LifecycleSemantics)
    with ch.connection() as reader:
        for event_ref, second_identity, expected in (
            ("other", 2, 1),
            ("same", 2, 0),
            ("same", 1, 1),
        ):
            source = f"SELECT tuple(toInt64(1)) entity_identity,toInt64(number+1) ordinal,if(number=0,'trigger_0','trigger_1') trigger_key,if(number=0,'same','{event_ref}') trigger_event_ref,tuple(toInt64(if(number=0,1,{second_identity}))) event_identity,toDateTime64('2026-02-01 00:00:00',6,'UTC') occurred_at FROM numbers(2)"
            query = f"WITH source AS ({source}),replay AS ({clickhouse_replay('source', semantics)}) SELECT * FROM ({clickhouse_confluence('source', 'replay', semantics)})"
            assert reader.query(query).result_rows == [(expected,)]
