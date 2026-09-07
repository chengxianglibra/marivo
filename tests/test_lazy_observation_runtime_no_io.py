"""Fresh-process acceptance of actual private semantic/source construction."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

_JOURNEY = r"""
import json
import os
import sys
from contextlib import ExitStack
from unittest.mock import patch

os.environ['MARIVO_TELEMETRY'] = 'off'
import marivo.analysis as mv
import marivo.analysis.session._runtime as runtime
import marivo.analysis.evidence.store as evidence_store
import marivo.datasource.backends as backends
from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.analysis.observation.predicates import eq, gt
from marivo.analysis.session.core import Session
from marivo.analysis.session._lazy_sources import make_lazy_sources
from marivo.analysis.session._store import SessionStore
from marivo.analysis.session._connections import AnalysisConnectionRuntime
from marivo.datasource.runtime import DatasourceConnectionService
from marivo.refs import ref
from tests.lazy_observation_fixtures import make_semantic_registry, NoIoActionPort

# Authoring inputs use the retained public helpers before observing the new
# private construction surface. Their current eager telemetry is not changed.
semantic_registry, sidecar = make_semantic_registry()
port = NoIoActionPort()
window = mv.time_scope(start='2026-02-01', end='2026-03-01')
selection = mv.time_scope(start='2026-01-01', end='2026-02-01')
day = mv.grain('day')
revenue = ref.metric('sales.revenue')
mean_amount = ref.metric('sales.mean_amount')
ratio = ref.metric('sales.conversion_rate')
api_value = ref.metric('sales.api_value')
region = ref.dimension('sales.customers.region')
order_time = ref.time_dimension('sales.orders.order_time')
orders = ref.entity('sales.orders')
snapshots = ref.entity('sales.snapshots')
validities = ref.entity('sales.validity')
api = ref.entity('sales.api')

# Imports load the installed runtime; all source/semantic construction below
# runs inside the observed boundary, with no real Session or backend bootstrap.
attempts = dict(datasource=0, connection=0, query=0, run=0, artifact=0,
                store=0, evidence=0, binding=0, filesystem=0, network=0)
active = False

def reject(kind):
    def blocked(*args, **kwargs):
        attempts[kind] += 1
        raise AssertionError('Observation attempted ' + kind)
    return blocked

def audit(event, args):
    if not active:
        return
    if event == 'open' or event.startswith(('os.mkdir', 'os.remove', 'os.rename',
            'os.rmdir', 'os.listdir', 'os.scandir', 'os.link', 'os.symlink',
            'os.truncate', 'sqlite3.connect')):
        reject('filesystem')()
    if event.startswith(('socket.', 'subprocess.', 'os.system', 'os.exec', 'os.spawn')):
        reject('network')()

sys.addaudithook(audit)
guards = (
    (backends, 'build_backend', 'datasource'),
    (backends, 'build_backend_with_secrets', 'datasource'),
    (DatasourceConnectionService, '__init__', 'connection'),
    (DatasourceConnectionService, 'session_backend', 'connection'),
    (DatasourceConnectionService, 'use_backend', 'connection'),
    (AnalysisConnectionRuntime, 'get_or_create', 'connection'),
    (AnalysisConnectionRuntime, 'record_query', 'query'),
    (AnalysisConnectionRuntime, 'remember_metric_artifact', 'binding'),
    (Session, '__init__', 'store'),
    (Session, 'observe', 'datasource'),
    (Session, 'source_bindings', 'binding'),
    (Session, 'artifact', 'artifact'),
    (SessionStore, '__init__', 'store'),
    (SessionStore, '_connect', 'store'),
    (SessionStore, 'begin_run', 'run'),
    (SessionStore, 'complete_run', 'run'),
    (SessionStore, 'fail_run', 'run'),
    (SessionStore, 'record_artifact', 'artifact'),
    (SessionStore, 'record_recovered_artifact', 'artifact'),
    (SessionStore, 'get_artifact', 'artifact'),
    (runtime, 'persist_frame', 'artifact'),
    (runtime, 'register_frame_artifact', 'artifact'),
    (runtime, 'persist_job_record', 'run'),
    (runtime, 'persist_reused_artifact_job', 'binding'),
    (evidence_store, 'open_evidence_store', 'evidence'),
    (evidence_store.EvidenceStore, '__init__', 'evidence'),
    (evidence_store.EvidenceStore, 'transaction', 'evidence'),
)
with ExitStack() as stack:
    for owner, name, kind in guards:
        stack.enter_context(patch.object(owner, name, reject(kind)))
    os.environ['MARIVO_TELEMETRY'] = 'on'
    active = True
    sources = make_lazy_sources(
        semantic_registry=semantic_registry, sidecar=sidecar, action_port=port,
        session_id='session-observation', store_id='store-observation',
    )
    population = sources.population(orders, time_scope=selection)
    population = population.where(eq(region, 'east'))
    observed = sources.observe(
        [revenue, mean_amount, ratio],
        population=population, time_scope=window,
    )
    filtered = observed.where(gt(observed.fields.metric(revenue), 0))
    result = filtered.with_dimensions(region).with_time_axis(
        order_time, grain=day
    ).aggregate().metric(revenue)
    tip = filtered
    for _ in range(80):
        tip = tip.where(gt(tip.fields.metric(revenue), 0))
    snapshot = sources.population(snapshots, time_scope=selection)
    validity = sources.population(validities, time_scope=selection)
    with sources.source_bindings({api: {'tenant': 'PRIVATE_CAPTURE_2A'}}):
        captured = sources.observe(api_value, time_scope=window)
    with sources.source_bindings({api: {'tenant': 'DIFFERENT_CAPTURE_2A'}}):
        changed = sources.observe(api_value, time_scope=window)
    assert captured.definition_fingerprint != changed.definition_fingerprint
    values = (population, observed, filtered, result, tip, snapshot, validity, captured)
    for value in values:
        assert value.schema is value.row_contract.schema
        assert value.state.kind == 'logical'
        rendered = value.contract().render()
        assert len(rendered.encode()) <= 8192
        assert len(repr(value)) <= 256
        assert 'PRIVATE_CAPTURE_2A' not in rendered + repr(value)
        assert 'PRIVATE_CAPTURE_2A' not in repr(value._root.parameters)
        assert 'PRIVATE_CAPTURE_2A' not in repr(value._lineage)
    negative_failures = 0
    for invalid in (
        lambda: sources.population(snapshots),
        lambda: sources.observe(api_value, time_scope=window),
        lambda: result.aggregate(),
        lambda: filtered.with_time_axis(order_time, grain=day).with_time_axis(
            order_time, grain=day),
        lambda: setattr(observed, 'kind', 'changed'),
    ):
        try:
            invalid()
        except DatasetConstructionError as error:
            assert error.expected and error.received and error.hint and error.repair
            negative_failures += 1
        else:
            raise AssertionError('Invalid definition was admitted')
    assert negative_failures == 5
    assert result.row_contract.shape_id.local_shape_id == 'dimension-time'
    assert [field.role_id for field in result.schema.columns][-1] == 'metric'
    assert not any(attempts.values()), attempts
    evidence = dict(
        session_id=observed._owner.session_id,
        first_fingerprint=observed.definition_fingerprint,
        final_fingerprint=result.definition_fingerprint,
        final_shape=str(result.row_contract.shape_id),
        deep_filter_nodes=80,
        checked_definitions=len(values),
        guarded_negative_failures=negative_failures,
        guarded_entrypoints=len(guards),
        telemetry_enabled=True,
        attempts=attempts,
    )
    active = False
print(json.dumps(evidence, sort_keys=True))
"""


def test_actual_private_observation_chain_is_pure() -> None:
    result = subprocess.run(
        [sys.executable, "-B", "-c", _JOURNEY],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    evidence = json.loads(result.stdout)
    assert evidence["final_shape"] == "metric/dimension-time@v1"
    assert evidence["deep_filter_nodes"] == 80
    assert evidence["checked_definitions"] == 8
    assert evidence["guarded_negative_failures"] == 5
    assert evidence["guarded_entrypoints"] == 27
    assert evidence["telemetry_enabled"] is True
    assert set(evidence["attempts"]) == {
        "datasource",
        "connection",
        "query",
        "run",
        "artifact",
        "store",
        "evidence",
        "binding",
        "filesystem",
        "network",
    }
    assert all(count == 0 for count in evidence["attempts"].values())
    print("Private Observation no-I/O acceptance: " + result.stdout.strip())
