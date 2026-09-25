"""Store-backed private J1 exchange and offline continuation."""

from __future__ import annotations

import os
import subprocess
import sys

import duckdb
import ibis
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.materialization.contracts import RunDatasetInput
from marivo.analysis.materialization.dsl_j1_artifact import (
    load_j1_artifact,
    publish_j1_artifact,
)
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.local_stage import run_j1_local
from marivo.analysis.materialization.source_stage import run_j1_source
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.materialization.writer_guard import session_writer_guard
from marivo.analysis.observation.dsl_j1 import J1Context, j1_row_contracts
from tests.shared_fixtures import DslCaseFactory


@pytest.mark.runtime
def test_j1_store_artifact_keeps_coordinate_state_offline(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = analysis_dsl_case_factory("j1")
    store = SessionStore(case.root)
    store.create_session("w3", session_ref="session")
    state = case.catalog._state
    context = J1Context(state.registry, state.sidecar, "session", store.store_id)
    domain = case.names.domain
    channel = ms.ref.dimension(f"{domain}.order.channel")
    observed = context.members(ms.ref.entity(f"{domain}.customer")).observe(
        ms.ref.metric(f"{domain}.revenue"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship(f"{domain}.order_buyer"),
        coordinates=(channel,),
    )
    backend = ibis.duckdb.connect(str(case.database_path))
    try:
        tables = {
            f"{domain}.customer": backend.table("customer"),
            f"{domain}.order": backend.table("order"),
        }
        source = run_j1_source(context, observed.root, backend, tables)
        expected = run_j1_source(context, observed.group_by(channel).root, backend, tables)
    finally:
        backend.disconnect()
    row, rows = j1_row_contracts(context, observed.root)
    with session_writer_guard(store.layout.lock_path("session"), session_ref="session"):
        run = store.admit(
            "session",
            "a" * 64,
            RunDatasetInput(
                observed.root.definition_fingerprint,
                row.shape_id,
                observed.root.row_contract_fingerprint,
                observed.root.row_set_contract_fingerprint,
                (observed.root.operator_id,),
                (f"{domain}.customer",),
            ),
        )
        published = publish_j1_artifact(
            store, run, observed, source, input_binding="j1.source.binding"
        )

    def forbidden_connect(*args: object, **kwargs: object) -> None:
        raise AssertionError("J1 cold continuation reopened DuckDB")

    monkeypatch.setattr(duckdb, "connect", forbidden_connect)
    restored = load_j1_artifact(
        SessionStore.open_existing(case.root),
        "session",
        published.artifact_ref,
        observed,
        input_binding="j1.source.binding",
    )
    group_node = observed.group_by(channel)
    grouped = run_j1_local(group_node.root, restored)
    assert sorted(grouped.primary.to_pylist(), key=lambda item: item["group"]) == sorted(
        expected.primary.to_pylist(), key=lambda item: item["group"]
    )
    group_row, _ = j1_row_contracts(context, group_node.root)
    with session_writer_guard(store.layout.lock_path("session"), session_ref="session"):
        local_run = store.admit(
            "session",
            "d" * 64,
            RunDatasetInput(
                group_node.root.definition_fingerprint,
                group_row.shape_id,
                group_node.root.row_contract_fingerprint,
                group_node.root.row_set_contract_fingerprint,
                (group_node.root.operator_id,),
                (),
            ),
            input_artifact_refs=(published.artifact_ref,),
        )
        local_artifact = publish_j1_artifact(
            store, local_run, group_node, grouped, input_binding="j1.local.binding"
        )
    local_reloaded = load_j1_artifact(
        SessionStore.open_existing(case.root),
        "session",
        local_artifact.artifact_ref,
        group_node,
        input_binding="j1.local.binding",
    )
    assert local_reloaded.primary.schema.equals(grouped.primary.schema)
    assert local_reloaded.primary.equals(grouped.primary)
    statistic = observed.summarize("count")
    statistic_result = run_j1_local(statistic.root, restored)
    statistic_row, _ = j1_row_contracts(context, statistic.root)
    with session_writer_guard(store.layout.lock_path("session"), session_ref="session"):
        statistic_run = store.admit(
            "session",
            "e" * 64,
            RunDatasetInput(
                statistic.root.definition_fingerprint,
                statistic_row.shape_id,
                statistic.root.row_contract_fingerprint,
                statistic.root.row_set_contract_fingerprint,
                (statistic.root.operator_id,),
                (),
            ),
            input_artifact_refs=(published.artifact_ref,),
        )
        statistic_artifact = publish_j1_artifact(
            store,
            statistic_run,
            statistic,
            statistic_result,
            input_binding="j1.statistic.binding",
        )
    statistic_reloaded = load_j1_artifact(
        SessionStore.open_existing(case.root),
        "session",
        statistic_artifact.artifact_ref,
        statistic,
        input_binding="j1.statistic.binding",
    )
    assert statistic_reloaded.primary.column("value").to_pylist() == [4]

    child = """
import duckdb
import sys
import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.materialization.dsl_j1_artifact import load_j1_artifact
from marivo.analysis.materialization.local_stage import run_j1_local
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.observation.dsl_j1 import J1Context

def forbidden(*args, **kwargs):
    raise AssertionError('cold J1 continuation opened DuckDB')

duckdb.connect = forbidden
root, artifact, domain = sys.argv[1:]
catalog = ms.load(workspace_dir=root)
store = SessionStore.open_existing(root)
state = catalog._state
context = J1Context(state.registry, state.sidecar, 'session', store.store_id)
channel = ms.ref.dimension(f'{domain}.order.channel')
observed = context.members(ms.ref.entity(f'{domain}.customer')).observe(
    ms.ref.metric(f'{domain}.revenue'),
    during=mv.time_scope(start='2026-08-01', end='2026-09-01'),
    via=ms.ref.relationship(f'{domain}.order_buyer'),
    coordinates=(channel,),
)
restored = load_j1_artifact(store, 'session', artifact, observed, input_binding='j1.source.binding')
grouped = run_j1_local(observed.group_by(channel).root, restored)
assert {(row['group'], row['value']) for row in grouped.primary.to_pylist()} == {
    ('web', 850), ('mobile', 150)
}
"""
    completed = subprocess.run(
        [sys.executable, "-c", child, str(case.root), published.artifact_ref, domain],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "MARIVO_PROJECT_ROOT": str(case.root)},
    )
    assert completed.returncode == 0, completed.stderr
    coordinate_receipt = next(
        part.storage_receipt
        for part in published.descriptor.retained_parts
        if part.role == "coordinate"
    )
    with (case.root / coordinate_receipt.project_relative_path / "data.parquet").open(
        "ab"
    ) as output:
        output.write(b"corrupt")
    with pytest.raises(MaterializationError):
        load_j1_artifact(
            SessionStore.open_existing(case.root),
            "session",
            published.artifact_ref,
            observed,
            input_binding="j1.source.binding",
        )


def test_j1_member_read_artifact_selects_without_source(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = analysis_dsl_case_factory("j1")
    store = SessionStore(case.root)
    store.create_session("w3-read", session_ref="session")
    state = case.catalog._state
    context = J1Context(state.registry, state.sidecar, "session", store.store_id)
    domain = case.names.domain
    read = context.members(ms.ref.entity(f"{domain}.customer")).read(
        ms.ref.dimension(f"{domain}.customer.region")
    )
    backend = ibis.duckdb.connect(str(case.database_path))
    try:
        source = run_j1_source(
            context,
            read.root,
            backend,
            {f"{domain}.customer": backend.table("customer")},
        )
    finally:
        backend.disconnect()
    row, _ = j1_row_contracts(context, read.root)
    with session_writer_guard(store.layout.lock_path("session"), session_ref="session"):
        run = store.admit(
            "session",
            "b" * 64,
            RunDatasetInput(
                read.root.definition_fingerprint,
                row.shape_id,
                read.root.row_contract_fingerprint,
                read.root.row_set_contract_fingerprint,
                (read.root.operator_id,),
                (f"{domain}.customer",),
            ),
        )
        published = publish_j1_artifact(store, run, read, source, input_binding="j1.read")
    monkeypatch.setattr(
        duckdb,
        "connect",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("source reopened")),
    )
    retained = load_j1_artifact(
        SessionStore.open_existing(case.root),
        "session",
        published.artifact_ref,
        read,
        input_binding="j1.read",
    )
    selected = run_j1_local(read.where(read.value.eq("east")).root, retained)
    assert selected.primary.column("member").to_pylist() == ["A", "B"]

    def fail_before_commit(point: str) -> None:
        if point == "before_commit":
            raise RuntimeError("injected publication failure")

    with session_writer_guard(store.layout.lock_path("session"), session_ref="session"):
        retry = store.admit(
            "session",
            "c" * 64,
            RunDatasetInput(
                read.root.definition_fingerprint,
                row.shape_id,
                read.root.row_contract_fingerprint,
                read.root.row_set_contract_fingerprint,
                (read.root.operator_id,),
                (f"{domain}.customer",),
            ),
        )
        with pytest.raises(RuntimeError, match="injected publication failure"):
            publish_j1_artifact(
                store, retry, read, source, input_binding="j1.read", event=fail_before_commit
            )
    assert store.run(retry.run_ref).lifecycle == "failed"
    assert store.lookup("session", "c" * 64) is None
    assert store.artifact(published.artifact_ref) is not None
    assert not store.resources("session")
