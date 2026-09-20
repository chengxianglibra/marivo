"""Opt-in real SELECT-only ClickHouse Distributed cluster Dataset acceptance.

Covers the real two-shard ``marivo_multisource`` topology: global aggregation
over a Distributed table with per-shard split proof, cross-shard duplicate
identity rejection, a shard-outage structured failure and recovery, a
FINAL/dedup-free receipt audit, ReplacingMergeTree unconverged acceptance, and
cross-shard relationship hit/miss fanout. Admin prepares per-shard fixtures;
the ``analysis_reader`` account executes every Dataset journey. Run this file
serially (``RUNTIME_WORKERS=1``): the shard-outage test stops a real shard
container, which would disturb concurrent tests reading the same shard.
"""

from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Iterator, Mapping
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pandas as pd
import pytest
from clickhouse_connect.driver.exceptions import DatabaseError

from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.observation.predicates import gt
from marivo.datasource.ir import TableSourceIR
from marivo.refs import ref
from marivo.semantic._expression_binding import CompiledExpressionSidecar
from marivo.semantic.validator import Registry
from tests.lazy_acceptance_capture import counts
from tests.lazy_scalar_source_fixtures import capture_submissions, registry_for
from tests.multisource_environment import clickhouse_analysis as clickhouse
from tests.multisource_environment.credentials import password

pytestmark = [
    pytest.mark.runtime,
    pytest.mark.skipif(
        os.environ.get("MARIVO_CLICKHOUSE_CLUSTER_TEST") != "1",
        reason="opt-in two-shard ClickHouse cluster",
    ),
]
REVENUE = ref.metric("sales.revenue")
CHANNEL = ref.dimension("sales.orders.channel")
CUSTOMER_REGION = ref.dimension("sales.customers.region")
DATABASE = "qualification_cluster"
CLUSTER_PORTS = clickhouse.CLUSTER_HTTP_PORTS
INITIATOR_PORT = 18201
# Hand-computed over create_cluster_tables rows (ids 0..4, amount = id + 0.25):
#   full sum = 0.25 + 1.25 + 2.25 + 3.25 + 4.25 = 11.25 with count 5
#   split_at=2 -> shard A ids {0, 1} sums to 1.5; shard B ids {2, 3, 4} sums
#   to 2.25 + 3.25 + 4.25 = 9.75; both halves together reproduce 11.25.
#   channel mutation: a/a | b/b/c -> group sums b 5.5, a 1.5, c 4.25.
#   fanout lines: (0, 1, 10.0) shard A; (1, 1, 2.0), (2, 2, 3.0), (3, 9, 20.0),
#   (4, 4, 30.0) shard B; every order row carries customer_id 1 ('EU').
#   relation hit sums by channel: a 10.0 + 2.0 = 12.0, b 3.0, c 30.0;
#   the order_id 9 line misses orders and lands under NULL region.
#   ReplacingMergeTree probe: key 1 at ver 1 amount 10.25 plus ver 2 amount
#   30.75 -> unconverged sum 41.0 over both physical rows.
DOCKER_CONFIG = str(Path.home() / ".cache/marivo-multisource/docker")
DOCKER_HOST = "unix://" + str(Path.home() / ".colima/marivo-multisource/docker.sock")


def docker(*args: str) -> None:
    """Run one docker command against the qualification Colima daemon."""
    subprocess.run(
        ("docker", "--config", DOCKER_CONFIG, *args),
        env={**os.environ, "DOCKER_HOST": DOCKER_HOST},
        check=True,
        capture_output=True,
    )


def wait_ready(port: int, *, deadline_seconds: float = 60) -> None:
    """Poll one loopback HTTP endpoint until it answers a reader SELECT."""
    deadline = time.monotonic() + deadline_seconds
    while True:
        try:
            with clickhouse.connection(port=port) as con:
                con.query("SELECT 1")
            return
        except Exception:
            if time.monotonic() > deadline:
                raise
            time.sleep(1)


def entity_registry(
    tmp_path: Path,
    tables: Mapping[str, str],
    *,
    decimal_amount: bool = True,
) -> tuple[Registry, CompiledExpressionSidecar]:
    """Point fixture entities at named qualification_cluster relations."""
    registry, sidecar = registry_for(Path("unused"), engine="clickhouse", table="unused")
    entities = dict(registry.entities)
    for path, table in tables.items():
        entity = entities[path]
        assert isinstance(entity.source, TableSourceIR)
        entities[path] = replace(
            entity,
            source=replace(
                entity.source,
                table=table,
                database=DATABASE,
                columns=tuple(
                    (
                        name,
                        replace(binding, data_type="decimal(9, 2)")
                        if name == "amount" and decimal_amount
                        else binding,
                    )
                    for name, binding in entity.source.columns
                ),
            ),
        )
    datasources = {
        name: replace(value, fields={**value.fields, "port": INITIATOR_PORT})
        for name, value in registry.datasources.items()
    }
    registry = replace(registry, entities=entities, datasources=datasources)
    registry.freeze()
    return registry, sidecar


def add_order_columns(suffix: str) -> None:
    """Extend the probe with channel and customer_id columns on both nodes."""
    for port in CLUSTER_PORTS:
        with clickhouse.connection(admin=True, port=port) as con:
            con.command(
                f"ALTER TABLE {DATABASE}.orders_{suffix}_distributed"
                " ADD COLUMN IF NOT EXISTS channel Nullable(String)"
            )
            con.command(
                f"ALTER TABLE {DATABASE}.orders_{suffix}_distributed"
                " ADD COLUMN IF NOT EXISTS customer_id Nullable(Int64)"
            )
            con.command(
                f"ALTER TABLE {DATABASE}.orders_{suffix}"
                " ADD COLUMN IF NOT EXISTS channel Nullable(String)"
            )
            con.command(
                f"ALTER TABLE {DATABASE}.orders_{suffix}"
                " ADD COLUMN IF NOT EXISTS customer_id Nullable(Int64)"
            )


def set_channels(suffix: str) -> None:
    """Assign deterministic channel values per shard without touching amounts."""
    plans: dict[int, str] = {
        18201: "UPDATE channel = 'a' WHERE id IN (0, 1) SETTINGS mutations_sync = 2",
        18203: ("UPDATE channel = if(id = 4, 'c', 'b') WHERE id >= 2 SETTINGS mutations_sync = 2"),
    }
    for port, mutation in plans.items():
        with clickhouse.connection(admin=True, port=port) as con:
            con.command(f"ALTER TABLE {DATABASE}.orders_{suffix} {mutation}")


@pytest.fixture(autouse=True)
def reader_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Expose the private fixture password through the declared env reference."""
    monkeypatch.setenv("MARIVO_TEST_CLICKHOUSE_PASSWORD", password())


@pytest.fixture
def probe_suffix() -> Iterator[str]:
    """Seed the canonical five-row split fixture on both shards; drop when done.

    Seeding runs inside the guarded span so a mid-seed failure still drops any
    partially created per-shard tables.
    """
    suffix = uuid4().hex
    try:
        clickhouse.create_cluster_tables(suffix, split_at=2)
        yield suffix
    finally:
        clickhouse.drop_cluster_tables(suffix)


def test_distributed_global_aggregation(tmp_path: Path, probe_suffix: str) -> None:
    suffix = probe_suffix
    add_order_columns(suffix)
    set_channels(suffix)
    registry, sidecar = entity_registry(tmp_path, {"sales.orders": f"orders_{suffix}_distributed"})
    runtime = DatasetRuntime.create(tmp_path, "distributed-global")
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    frame = (
        sources.observe((REVENUE, ref.metric("sales.order_count")))
        .aggregate()
        .execute()
        .to_pandas()
    )
    assert frame.revenue.tolist() == [11.25]
    assert frame.order_count.tolist() == [5]
    grouped = sources.observe(REVENUE).with_dimensions(CHANNEL).aggregate().where(gt(REVENUE, 0))
    assert grouped.rank(grouped.fields.metric(REVENUE)).execute().to_pandas()[
        "revenue"
    ].tolist() == [5.5, 4.25, 1.5]
    # The ranked grouped action transferred exactly the three group rows.
    assert runtime.statistics.primary_queries == 1
    assert runtime.statistics.transferred_rows == 3
    # Fanout proof: each shard's local table holds exactly its split half and
    # both halves together reproduce the global sum.
    with clickhouse.connection(port=18201) as con:
        assert con.query(
            f"SELECT sum(amount), count() FROM {DATABASE}.orders_{suffix}"
        ).first_row == (1.5, 2)
    with clickhouse.connection(port=18203) as con:
        assert con.query(
            f"SELECT sum(amount), count() FROM {DATABASE}.orders_{suffix}"
        ).first_row == (9.75, 3)
    assert runtime.store.resources(runtime.session_ref) == ()


def test_cross_shard_duplicate_identity_rejected(tmp_path: Path, probe_suffix: str) -> None:
    suffix = probe_suffix
    with clickhouse.connection(admin=True, port=18201) as con:
        con.command(f"INSERT INTO {DATABASE}.orders_{suffix} VALUES (10, '10.25')")
    with clickhouse.connection(admin=True, port=18203) as con:
        con.command(f"INSERT INTO {DATABASE}.orders_{suffix} VALUES (10, '10.25')")
    registry, sidecar = entity_registry(tmp_path, {"sales.orders": f"orders_{suffix}_distributed"})
    runtime = DatasetRuntime.create(tmp_path, "distributed-duplicate")
    target = (
        runtime.sources(semantic_registry=registry, sidecar=sidecar).observe(REVENUE).aggregate()
    )
    with pytest.raises(MaterializationError, match="source_row_unique"):
        target.execute()
    assert counts(runtime)["dataset_artifacts"] == 0
    assert runtime.store.resources(runtime.session_ref) == ()
    assert runtime.statistics.events.get("remote_read_status_unknown") == 1


def test_shard_outage_fails_structured_and_recovers(tmp_path: Path, probe_suffix: str) -> None:
    suffix = probe_suffix
    container = "marivo-multisource-clickhouse-shard-b-1"
    registry, sidecar = entity_registry(tmp_path, {"sales.orders": f"orders_{suffix}_distributed"})
    runtime = DatasetRuntime.create(tmp_path, "distributed-outage")
    target = (
        runtime.sources(semantic_registry=registry, sidecar=sidecar).observe(REVENUE).aggregate()
    )
    try:
        docker("stop", container)
        # The initiator reports the dead shard as a server error response, which
        # the driver raises as a bare DatabaseError; a mid-stream transport cut
        # would instead surface wrapped as MaterializationError with __cause__
        # preserved. Either way the Run record below carries the structured
        # failure with the original driver error class recorded on the failed
        # submission receipt, and nothing is published.
        with pytest.raises((MaterializationError, DatabaseError)):
            target.execute()
    finally:
        # Restart even if the stop itself failed, and let wait_ready surface a
        # restart problem instead of masking it behind a success status.
        docker("start", container)
        wait_ready(18203, deadline_seconds=90)
        wait_ready(18201)
    assert runtime.last_run_ref is not None
    run = runtime.store.run(runtime.last_run_ref)
    assert run is not None and run.lifecycle == "failed"
    assert run.output_artifact_ref is None
    assert run.failure is not None
    assert run.failure.kind == "execution_failed"
    # Original-error preservation, bounded by the safe RunFailure contract: the
    # persisted payload records the projected phase and the generic safe
    # message; the full driver message is intentionally sanitized. The
    # preservation evidence is the failed submission receipt, which must carry
    # the original driver error class (DatabaseError, not a Marivo wrapper) on
    # the exact identity-assertion statement that fanned out to the dead shard.
    assert run.failure.phase == "authority_resolution"
    assert run.failure.safe_message == "The Dataset action failed before publication."
    failed_submissions = [
        submission for submission in runtime.statistics.submissions if submission.state == "failed"
    ]
    assert failed_submissions, "Expected at least one failed submission receipt"
    assert failed_submissions[0].role == "validation_batch"
    assert failed_submissions[0].error_type == DatabaseError.__name__
    assert f"orders_{suffix}_distributed" in failed_submissions[0].sql
    assert counts(runtime)["dataset_artifacts"] == 0
    assert runtime.store.resources(runtime.session_ref) == ()
    assert runtime.statistics.events.get("remote_read_status_unknown") == 1
    assert target.execute().to_pandas().revenue.tolist() == [11.25]
    # Statistics reset per action: the retry alone ran one fresh primary query.
    assert runtime.statistics.primary_queries == 1
    assert counts(runtime)["dataset_artifacts"] == 1


def test_receipt_audit_free_of_dedup_clauses(tmp_path: Path, probe_suffix: str) -> None:
    suffix = probe_suffix
    patch = pytest.MonkeyPatch()
    submitted = capture_submissions(patch)
    try:
        registry, sidecar = entity_registry(
            tmp_path, {"sales.orders": f"orders_{suffix}_distributed"}
        )
        runtime = DatasetRuntime.create(tmp_path, "distributed-receipts")
        frame = (
            runtime.sources(semantic_registry=registry, sidecar=sidecar)
            .observe((REVENUE, ref.metric("sales.order_count")))
            .aggregate()
            .execute()
            .to_pandas()
        )
        assert frame.revenue.tolist() == [11.25]
        duplicate_runtime = DatasetRuntime.create(tmp_path, "distributed-receipts-duplicate")
        with clickhouse.connection(admin=True, port=18201) as con:
            con.command(f"INSERT INTO {DATABASE}.orders_{suffix} VALUES (10, '10.25')")
        with clickhouse.connection(admin=True, port=18203) as con:
            con.command(f"INSERT INTO {DATABASE}.orders_{suffix} VALUES (10, '10.25')")
        with pytest.raises(MaterializationError, match="source_row_unique"):
            duplicate_runtime.sources(semantic_registry=registry, sidecar=sidecar).observe(
                REVENUE
            ).aggregate().execute()
        sql_texts = [str(entry["sql"]) for entry in submitted]
        assert sql_texts, "Expected captured driver submissions"
        for banned in ("FINAL", "final = 1", "OPTIMIZE", " DEDUPLICATE", " any("):
            assert not any(banned in sql for sql in sql_texts), banned
        # Lowercase sweep over the same receipts: verified against the widest
        # compiled journeys on this backend (grouped + ranked + validated), the
        # captured SQL contains no identifier that collides with these tokens,
        # so plain substring checks need no identifier-aware regex.
        lowered = " ".join(sql.lower() for sql in sql_texts)
        for banned in ("final", "optimize", "deduplicate", "any("):
            assert banned not in lowered, banned
        for runtime_under_audit in (runtime, duplicate_runtime):
            assert runtime_under_audit.store.resources(runtime_under_audit.session_ref) == ()
    finally:
        # Unpatch the adapters before any cleanup I/O so cleanup runs against
        # the unmodified driver; the probe_suffix fixture's drop_cluster_tables
        # removes the duplicated rows together with the tables.
        patch.undo()


def test_replacing_merge_tree_unconverged_sum() -> None:
    """Unconverged ReplacingMergeTree reads stay plain SQL: both rows sum.

    The governed Dataset journey rejects duplicate identity by contract (the
    cross-shard duplicate test above); this probe pins the engine-facing
    contract instead — Marivo's execution adapter reads the ReplacingMergeTree
    relation without FINAL, OPTIMIZE, or dedup rewriting, so the unconverged
    sum of both physical versions (10.25 + 30.75 = 41.0) is accepted as the
    correct-by-contract unstable read.
    """
    from decimal import Decimal

    import ibis

    from marivo.analysis.materialization.clickhouse_execution import ClickHouseExecutionAdapter

    table = "orders_replacing_" + uuid4().hex
    try:
        with clickhouse.connection(admin=True, port=INITIATOR_PORT) as con:
            con.command(
                f"CREATE TABLE {DATABASE}.{table}"
                " (id Int64, amount Decimal(9, 2), ver UInt64)"
                " ENGINE = ReplacingMergeTree(ver) ORDER BY id"
            )
            con.command(f"GRANT SELECT ON {DATABASE}.{table} TO analysis_reader")
            con.command(f"INSERT INTO {DATABASE}.{table} VALUES (1, '10.25', 1)")
            con.command(f"INSERT INTO {DATABASE}.{table} VALUES (1, '30.75', 2)")
        with clickhouse.connection(port=INITIATOR_PORT) as con:
            adapter = ClickHouseExecutionAdapter(ibis.clickhouse.from_connection(con))
            try:
                submitted: list[str] = []
                adapter.observe(lambda submission: submitted.append(submission.sql), "source")
                source = ibis.clickhouse.from_connection(con).table(table, database=DATABASE)
                result = adapter.read_table(source.aggregate(revenue=source.amount.sum()))
                assert result.to_pylist() == [{"revenue": Decimal("41.00")}]
                assert submitted
                for banned in ("FINAL", "final = 1", "OPTIMIZE", " DEDUPLICATE", " any("):
                    assert not any(banned in sql for sql in submitted), banned
                lowered = " ".join(sql.lower() for sql in submitted)
                for banned in ("final", "optimize", "deduplicate", "any("):
                    assert banned not in lowered, banned
            finally:
                adapter.disconnect()
    finally:
        with clickhouse.connection(admin=True, port=INITIATOR_PORT) as con:
            con.command(f"DROP TABLE IF EXISTS {DATABASE}.{table}")


def test_cross_shard_relation_hit_and_miss(tmp_path: Path) -> None:
    suffix = uuid4().hex
    try:
        clickhouse.create_cluster_tables(suffix, split_at=2)
        seed_fanout(suffix)
        registry, sidecar = entity_registry(
            tmp_path,
            {
                "sales.orders": f"orders_{suffix}_distributed",
                "sales.lines": f"lines_{suffix}_distributed",
                "sales.customers": f"customers_{suffix}_distributed",
            },
        )
        runtime = DatasetRuntime.create(tmp_path, "distributed-fanout")
        sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
        hit = (
            sources.observe(ref.metric("sales.line_revenue"))
            .with_dimensions(CHANNEL)
            .aggregate()
            .execute()
            .to_pandas()
            .sort_values("channel")
        )
        # order_id 1 -> channel a (lines 10.0 + 2.0); order_id 2 -> b (3.0);
        # order_id 4 -> c (30.0); the order_id 9 line is a cross-shard miss and
        # lands in the NULL channel group.
        assert hit.channel.tolist() == ["a", "b", "c", pd.NA]
        assert hit.line_revenue.tolist() == [12.0, 3.0, 30.0, 20.0]
        miss = (
            sources.observe(ref.metric("sales.line_revenue"))
            .with_dimensions(CUSTOMER_REGION)
            .aggregate()
            .execute()
            .to_pandas()
        )
        # customer_id 1 ('EU') owns orders 1, 2, 4 (lines 12 + 3 + 30 = 45);
        # the missed line keeps its NULL region group.
        assert sorted(miss.line_revenue.tolist()) == [20.0, 45.0]
        assert runtime.store.resources(runtime.session_ref) == ()
    finally:
        for port in CLUSTER_PORTS:
            with clickhouse.connection(admin=True, port=port) as con:
                con.command(f"DROP TABLE IF EXISTS {DATABASE}.lines_{suffix}_distributed")
                con.command(f"DROP TABLE IF EXISTS {DATABASE}.lines_{suffix}")
                con.command(f"DROP TABLE IF EXISTS {DATABASE}.customers_{suffix}_distributed")
                con.command(f"DROP TABLE IF EXISTS {DATABASE}.customers_{suffix}")
        clickhouse.drop_cluster_tables(suffix)


def seed_fanout(suffix: str) -> None:
    """Prepare channel/customer-bearing orders plus cross-shard lines and customers."""
    plans: dict[int, tuple[str, ...]] = {
        18201: (
            f"ALTER TABLE {DATABASE}.orders_{suffix}_distributed"
            " ADD COLUMN IF NOT EXISTS channel Nullable(String)",
            f"ALTER TABLE {DATABASE}.orders_{suffix}_distributed"
            " ADD COLUMN IF NOT EXISTS customer_id Nullable(Int64)",
            f"ALTER TABLE {DATABASE}.orders_{suffix}"
            " ADD COLUMN IF NOT EXISTS channel Nullable(String)",
            f"ALTER TABLE {DATABASE}.orders_{suffix}"
            " ADD COLUMN IF NOT EXISTS customer_id Nullable(Int64)",
            f"ALTER TABLE {DATABASE}.orders_{suffix}"
            " UPDATE channel = 'a', customer_id = 1 WHERE id IN (0, 1)"
            " SETTINGS mutations_sync = 2",
            f"CREATE TABLE {DATABASE}.lines_{suffix}"
            " (id Int64, order_id Nullable(Int64), amount Nullable(Decimal(9, 2)))"
            " ENGINE = MergeTree ORDER BY id",
            f"CREATE TABLE {DATABASE}.lines_{suffix}_distributed"
            f" AS {DATABASE}.lines_{suffix}"
            f" ENGINE = Distributed(marivo_multisource, {DATABASE},"
            f" lines_{suffix}, rand())",
            f"INSERT INTO {DATABASE}.lines_{suffix} VALUES (0, 1, 10.0)",
            f"CREATE TABLE {DATABASE}.customers_{suffix}"
            " (id Int64, region Nullable(String)) ENGINE = MergeTree ORDER BY id",
            f"CREATE TABLE {DATABASE}.customers_{suffix}_distributed"
            " (id Int64, region Nullable(String))"
            f" ENGINE = Distributed(marivo_multisource, {DATABASE},"
            f" customers_{suffix}, rand())",
            f"INSERT INTO {DATABASE}.customers_{suffix} VALUES (1, 'EU')",
            f"GRANT SELECT ON {DATABASE}.lines_{suffix} TO analysis_reader",
            f"GRANT SELECT ON {DATABASE}.lines_{suffix}_distributed TO analysis_reader",
            f"GRANT SELECT ON {DATABASE}.customers_{suffix} TO analysis_reader",
            f"GRANT SELECT ON {DATABASE}.customers_{suffix}_distributed TO analysis_reader",
        ),
        18203: (
            f"ALTER TABLE {DATABASE}.orders_{suffix}_distributed"
            " ADD COLUMN IF NOT EXISTS channel Nullable(String)",
            f"ALTER TABLE {DATABASE}.orders_{suffix}_distributed"
            " ADD COLUMN IF NOT EXISTS customer_id Nullable(Int64)",
            f"ALTER TABLE {DATABASE}.orders_{suffix}"
            " ADD COLUMN IF NOT EXISTS channel Nullable(String)",
            f"ALTER TABLE {DATABASE}.orders_{suffix}"
            " ADD COLUMN IF NOT EXISTS customer_id Nullable(Int64)",
            f"ALTER TABLE {DATABASE}.orders_{suffix}"
            " UPDATE channel = if(id = 4, 'c', 'b'), customer_id = 1 WHERE id >= 2"
            " SETTINGS mutations_sync = 2",
            f"CREATE TABLE {DATABASE}.lines_{suffix}"
            " (id Int64, order_id Nullable(Int64), amount Nullable(Decimal(9, 2)))"
            " ENGINE = MergeTree ORDER BY id",
            f"CREATE TABLE {DATABASE}.lines_{suffix}_distributed"
            f" AS {DATABASE}.lines_{suffix}"
            f" ENGINE = Distributed(marivo_multisource, {DATABASE},"
            f" lines_{suffix}, rand())",
            f"INSERT INTO {DATABASE}.lines_{suffix}"
            " VALUES (1, 1, 2.0), (2, 2, 3.0), (3, 9, 20.0), (4, 4, 30.0)",
            f"CREATE TABLE {DATABASE}.customers_{suffix}"
            " (id Int64, region Nullable(String)) ENGINE = MergeTree ORDER BY id",
            f"CREATE TABLE {DATABASE}.customers_{suffix}_distributed"
            " (id Int64, region Nullable(String))"
            f" ENGINE = Distributed(marivo_multisource, {DATABASE},"
            f" customers_{suffix}, rand())",
            f"GRANT SELECT ON {DATABASE}.lines_{suffix} TO analysis_reader",
            f"GRANT SELECT ON {DATABASE}.lines_{suffix}_distributed TO analysis_reader",
            f"GRANT SELECT ON {DATABASE}.customers_{suffix} TO analysis_reader",
            f"GRANT SELECT ON {DATABASE}.customers_{suffix}_distributed TO analysis_reader",
        ),
    }
    for port, statements in plans.items():
        with clickhouse.connection(admin=True, port=port) as con:
            for statement in statements:
                con.command(statement)


def test_cluster_reader_account() -> None:
    evidence = clickhouse.setup_cluster()
    assert evidence["shards"] == 2
    for port in CLUSTER_PORTS:
        denied = evidence[f"denied_{port}"]
        assert isinstance(denied, list) and len(denied) == 5
