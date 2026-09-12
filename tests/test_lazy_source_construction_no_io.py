"""Fresh-process I/O guards around the complete private Slice 3a construction surface."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

# Existing Dataset/Observation no-I/O journeys own deep graphs and the eager
# Session persistence matrix. This journey adds the source algebra boundaries.
_JOURNEY = r"""
import json
import os
import sys
from contextlib import ExitStack
from dataclasses import FrozenInstanceError, replace
from unittest.mock import patch

os.environ['MARIVO_TELEMETRY'] = 'off'
import ibis
import pyarrow as pa
import pyarrow.parquet as pq
from ibis.backends.duckdb import Backend
import marivo.analysis as mv
import marivo.semantic.runtime_metric_lowering
import marivo.analysis.compiler as compiler
import marivo.analysis.compiler.lowering as lowering
import marivo.analysis.materialization.admission as admission
import marivo.analysis.materialization.storage as storage
import marivo.analysis.observation.ordering
import marivo.analysis.operators.compare
import marivo.analysis.operators.correlate
import marivo.analysis.operators.discovery
import marivo.analysis.operators.forecast
import marivo.analysis.operators.attribute
import marivo.analysis.operators.attribute_expansion
import marivo.datasource.backends as backends
import marivo.datasource.metadata as metadata
import marivo.datasource.secrets as secrets
from marivo._temporal import builtin_grain, semantic_grain
from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.observation.contracts import metric_definition
from marivo.analysis.observation.predicates import (
    all_of, any_of, eq, gt, gte, is_in, is_not_null, is_null, lt, lte, not_, not_eq,
)
from marivo.analysis.observation.sampling import engine_sample
from marivo.analysis.session._lazy_sources import make_lazy_sources
from marivo.analysis.session.core import Session
from marivo.datasource.ir import CsvSourceIR
from marivo.datasource.runtime import DatasourceConnectionService
from marivo.refs import ref
from marivo.semantic.ir import SemiAdditive, TimeFoldIR, TimestampParse
from marivo.semantic.validator import Registry
from tests.lazy_observation_fixtures import NoIoActionPort, make_semantic_registry

# Build only authored IR and public helper inputs before the boundary. Source
# normalization, selectors, predicates and every Dataset are built under guards.
base, sidecar = make_semantic_registry()
window = mv.time_scope(start='2026-02-01', end='2026-03-01')
revenue = ref.metric('sales.revenue')
weighted = ref.metric('sales.weighted_amount')
region = ref.dimension('sales.customers.region')
order_time = ref.time_dimension('sales.orders.order_time')
customers = ref.entity('sales.customers')
grains = tuple(builtin_grain(unit) for unit in (
    'second', 'minute', 'hour', 'day', 'week', 'month', 'quarter', 'year',
)) + (builtin_grain('minute', count=5),)
uncertified = semantic_grain(calendar=ref.period_calendar('sales.fiscal'), level='month')

def copy_registry():
    return Registry(
        domains=dict(base.domains), datasources=dict(base.datasources),
        entities=dict(base.entities), dimensions=dict(base.dimensions),
        measures=dict(base.measures), metrics=dict(base.metrics),
        relationships=dict(base.relationships),
    )

aggregate_registries = []
for aggregation in ('sum', 'count', 'mean', 'min', 'max', 'count_distinct',
                    'median', ('percentile', 0.9)):
    registry = copy_registry()
    registry.metrics[revenue.path] = replace(
        registry.metrics[revenue.path], aggregation=aggregation,
    )
    registry.freeze()
    aggregate_registries.append(registry)

timestamp_registry = copy_registry()
entity = timestamp_registry.entities['sales.orders']
assert isinstance(entity.source, CsvSourceIR)
timestamp_registry.entities[entity.semantic_id] = replace(
    entity, source=replace(entity.source, schema=tuple(
        (name, 'timestamp' if name == 'day' else kind)
        for name, kind in entity.source.schema
    )),
)
timestamp_registry.dimensions[order_time.path] = replace(
    timestamp_registry.dimensions[order_time.path], granularity='second',
    parse=TimestampParse(timezone='UTC'),
)
timestamp_registry.freeze()
fold_registry = copy_registry()
fold_registry.measures['sales.orders.amount'] = replace(
    fold_registry.measures['sales.orders.amount'],
    additivity=SemiAdditive(order_time.path, TimeFoldIR('last')),
)
fold_registry.freeze()
port = NoIoActionPort()

attempts = dict(source=0, backend=0, credentials=0, metadata=0, query=0,
                compiler=0, arrow=0, parquet=0, session=0, store=0, run=0,
                artifact=0, filesystem=0, network=0)
active = False

def reject(kind):
    def blocked(*args, **kwargs):
        attempts[kind] += 1
        raise AssertionError('Source construction attempted ' + kind)
    return blocked

def audit(event, args):
    if not active:
        return
    if event == 'open' or event.startswith((
        'os.mkdir', 'os.remove', 'os.rename', 'os.rmdir', 'os.listdir',
        'os.scandir', 'os.link', 'os.symlink', 'os.truncate', 'sqlite3.connect',
    )):
        reject('filesystem')()
    if event.startswith(('socket.', 'subprocess.', 'os.system', 'os.exec', 'os.spawn')):
        reject('network')()

sys.addaudithook(audit)
guards = (
    (backends, 'build_backend', 'backend'),
    (backends, 'build_backend_with_secrets', 'backend'),
    (backends, '_build_backend_from_effective', 'backend'),
    (backends, '_effective_kwargs', 'credentials'),
    (admission, '_build_backend_from_effective', 'backend'),
    (admission, '_effective_kwargs', 'credentials'),
    (secrets, 'resolve', 'credentials'),
    (secrets.EnvProvider, 'get', 'credentials'),
    (secrets.LocalPlaintextCache, 'get', 'credentials'),
    (metadata, 'inspect_table', 'metadata'),
    (metadata, '_query_rows', 'metadata'),
    (ibis, 'connect', 'backend'),
    (ibis, 'memtable', 'arrow'),
    (Backend, '__init__', 'backend'),
    (Backend, 'connect', 'backend'),
    (Backend, 'table', 'metadata'),
    (Backend, 'execute', 'query'),
    (Backend, 'raw_sql', 'query'),
    (Backend, 'to_pyarrow', 'arrow'),
    (Backend, 'to_pyarrow_batches', 'arrow'),
    (Backend, 'read_csv', 'source'),
    (Backend, 'read_parquet', 'source'),
    (Backend, 'read_json', 'source'),
    (admission, 'read_json_source', 'source'),
    (compiler, 'compile_dataset', 'compiler'),
    (lowering, 'compile_dataset', 'compiler'),
    (admission, 'compile_dataset', 'compiler'),
    (DatasetRuntime, '__init__', 'session'),
    (DatasetRuntime, 'create', 'session'),
    (DatasetRuntime, 'open', 'session'),
    (DatasetRuntime, 'execute_metric', 'run'),
    (DatasetRuntime, 'execute_population', 'run'),
    (DatasetRuntime, 'execute_delta', 'run'),
    (SessionStore, '__init__', 'store'),
    (SessionStore, '_connection', 'store'),
    (SessionStore, 'admit', 'run'),
    (SessionStore, 'reserve', 'run'),
    (SessionStore, 'publish', 'artifact'),
    (Session, '__init__', 'session'),
    (DatasourceConnectionService, '__init__', 'backend'),
    (DatasourceConnectionService, 'session_backend', 'backend'),
    (DatasourceConnectionService, 'use_backend', 'backend'),
    (storage, 'read_primary', 'artifact'),
    (storage, 'read_preview', 'artifact'),
    (storage, 'write_local_dataset', 'artifact'),
    (admission, 'read_primary', 'artifact'),
    (admission, 'read_preview', 'artifact'),
    (admission, 'write_local_dataset', 'artifact'),
    (pa, 'array', 'arrow'),
    (pa, 'table', 'arrow'),
    (pa, 'record_batch', 'arrow'),
    (pa, 'OSFile', 'arrow'),
    (pa, 'memory_map', 'arrow'),
    (pa, 'BufferReader', 'arrow'),
    (pq, 'read_table', 'parquet'),
    (pq, 'write_table', 'parquet'),
    (pq, 'ParquetFile', 'parquet'),
    (pq, 'ParquetWriter', 'parquet'),
    (os, 'stat', 'filesystem'),
    (os, 'lstat', 'filesystem'),
)

def sources_for(registry):
    return make_lazy_sources(
        semantic_registry=registry, sidecar=sidecar, action_port=port,
        session_id='source-construction', store_id='source-construction',
    )

with ExitStack() as stack:
    for owner, name, kind in guards:
        stack.enter_context(patch.object(owner, name, reject(kind)))
    os.environ['MARIVO_TELEMETRY'] = 'on'
    active = True
    try:
        sources = sources_for(base)
        original = sources.observe(revenue, time_scope=window)
        original_state = (original.definition_fingerprint, original._root,
                          original.row_contract, original.row_set_contract)
        dimensional = original.with_dimensions(region)
        metric = dimensional.fields.metric(revenue)
        private = 'PRIVATE_SOURCE_LITERAL_3A_6317'
        predicates = (
            eq(metric, 5), not_eq(metric, 5), lt(metric, 5), lte(metric, 5),
            gt(metric, 5), gte(metric, 5), is_in(metric, [5, 7]),
            is_null(metric), is_not_null(metric),
            all_of(gt(metric, 1), lt(metric, 9)),
            any_of(eq(region, private), is_null(region)), not_(eq(metric, 5)),
        )
        filtered = tuple(dimensional.where(predicate) for predicate in predicates)
        assert len({value.definition_fingerprint for value in filtered}) == 12
        assert all(value.row_contract == dimensional.row_contract for value in filtered)
        assert all(value.row_set_contract == dimensional.row_set_contract for value in filtered)
        canonical = dimensional.where(all_of(
            gt(metric, 1), any_of(eq(region, private), is_null(region)),
        ))
        equivalent = dimensional.where(
            any_of(is_null(region), eq(region, private)), gt(metric, 1), gt(metric, 1),
        )
        assert canonical.definition_fingerprint == equivalent.definition_fingerprint
        changed = dimensional.where(any_of(eq(region, private + '_changed'), is_null(region)))
        assert changed.definition_fingerprint != filtered[10].definition_fingerprint

        eligible = sources.population(customers).where(any_of(
            is_in(region, [private, 'west']), is_null(region),
        ))
        policy = engine_sample(target_rows=3, seed=631798245)
        sampled = eligible.sample(policy)
        assert sampled.definition_fingerprint == eligible.sample(
            engine_sample(target_rows=3, seed=631798245),
        ).definition_fingerprint
        assert sampled.definition_fingerprint != eligible.sample(
            engine_sample(target_rows=3, seed=631798246),
        ).definition_fingerprint
        assert sampled.row_set_contract == eligible.row_set_contract
        sampled_metrics = sources.observe([revenue, weighted], population=sampled, time_scope=window)
        reduced = sampled_metrics.with_dimensions(region).aggregate().metric(revenue)

        timed = sources_for(timestamp_registry).observe(revenue, time_scope=window)
        grain_results = tuple(timed.with_time_axis(order_time, grain=grain).aggregate()
                              for grain in grains)
        assert len({value.definition_fingerprint for value in grain_results}) == len(grains)
        assert tuple(metric_definition(value).grain for value in grain_results) == grains
        aggregate_results = tuple(sources_for(registry).observe(
            revenue, time_scope=window,
        ).with_dimensions(region).aggregate() for registry in aggregate_registries)
        assert len({value.definition_fingerprint for value in aggregate_results}) == 8
        folded = sources_for(fold_registry).observe(revenue, time_scope=window).with_time_axis(
            order_time, grain=builtin_grain('month'),
        ).aggregate()

        ranked = tuple(reduced.rank(reduced.fields.metric(revenue), ties=ties)
                       for ties in ('ordinal', 'dense', 'min', 'max'))
        assert len({value.definition_fingerprint for value in ranked}) == 4
        limited = tuple(value.where(gte(value.fields.get('rank'), 2)).limit(3).limit(2)
                        for value in ranked)
        assert all(value.row_set_contract.cardinality.row_bound.max_rows == 2 for value in limited)
        assert all(value.row_contract == rank.row_contract for value, rank in zip(limited, ranked))
        assert original_state == (original.definition_fingerprint, original._root,
                                  original.row_contract, original.row_set_contract)
        assert len(original.schema.columns) == 2
        comparison_inputs = (
            original, original.aggregate(), reduced, grain_results[0],
            dimensional.with_time_axis(order_time, grain=builtin_grain('day')).aggregate(),
        )
        comparisons = tuple(value.compare(value) for value in comparison_inputs)
        assert tuple(value.row_contract.shape_id.local_shape_id for value in comparisons) == (
            'entity', 'scalar', 'dimension', 'time', 'dimension-time',
        )
        delta_filtered = comparisons[2].where(eq(
            comparisons[2].fields.get('coordinate_presence'), 'matched',
        ))
        delta_ranked = delta_filtered.rank(delta_filtered.fields.get('delta'))
        delta_limited = delta_ranked.limit(2)
        assert delta_limited._root.operator_id == 'delta.limit'
        attributed = comparisons[2].attribute(axes=[region])
        component_attributed = aggregate_results[2].compare(aggregate_results[2]).attribute(axes=[region])
        expanded_attributed = comparisons[1].attribute(axes=[region])
        attributed_selected = attributed.where(eq(attributed.fields.get('active_axis_mask'), (True,)))
        assert 'metric.expand_axes' not in original.contract().render()
        assert 'delta.attribute_expanded' not in comparisons[1].contract().render()
        scalar_delta = comparisons[1]

        negative_failures = 0
        for invalid in (
            lambda: dimensional.where(eq(metric, True)),
            lambda: dimensional.where(gt(region, private)),
            lambda: dimensional.where(eq(metric, 1), gt(metric, 2)),
            lambda: original.where(eq(region, private)),
            lambda: engine_sample(target_rows=True),
            lambda: sampled.sample(policy),
            lambda: sampled.where(eq(region, private)),
            lambda: original.with_time_axis(order_time, grain=builtin_grain('hour')),
            lambda: original.with_time_axis(order_time, grain=uncertified),
            lambda: original.limit(2),
            lambda: ranked[0].rank(ranked[0].fields.metric(revenue)),
            lambda: dimensional.rank(dimensional.fields.metric(revenue)),
            lambda: reduced.rank(reduced.fields.dimension(region)),
            lambda: ranked[0].limit(True),
            lambda: scalar_delta.where(gt(scalar_delta.fields.get('delta'), 0)),
            lambda: scalar_delta.rank(scalar_delta.fields.get('delta')),
            lambda: scalar_delta.limit(1),
            lambda: setattr(original, 'kind', 'changed'),
            lambda: setattr(policy, 'target_rows', 4),
        ):
            try:
                invalid()
            except DatasetConstructionError as error:
                assert error.expected and error.received and error.repair
                assert private not in str(error)
                negative_failures += 1
            except FrozenInstanceError:
                negative_failures += 1
            else:
                raise AssertionError('Invalid source construction was admitted')

        values = (original, dimensional, *filtered, canonical, eligible, sampled,
                  sampled_metrics, reduced, *grain_results, *aggregate_results,
                  folded, *ranked, *limited, *comparisons,
                  delta_filtered, delta_ranked, delta_limited, attributed, component_attributed,
                  expanded_attributed, attributed_selected)
        for index, value in enumerate(values):
            assert value.state.kind == 'logical'
            assert value.schema is value.row_contract.schema
            surfaces = dict(repr=repr(value), contract=value.contract().render(),
                            parameters=repr(value._root.parameters))
            for name, visible in surfaces.items():
                assert private not in visible, (index, name, 'predicate disclosure')
                if name != 'parameters':
                    assert '631798245' not in visible, (index, name, 'seed disclosure')
            assert len(repr(value)) <= 256
            assert len(value.contract().render().encode()) <= 8192
        assert not any(attempts.values()), attempts
        evidence = dict(
            predicates=len(predicates), grains=len(grains), aggregates=len(aggregate_results),
            ties=len(ranked), checked_definitions=len(values),
            guarded_negative_failures=negative_failures, guarded_entrypoints=len(guards),
            attempts=attempts, telemetry_enabled=True,
        )
    finally:
        active = False
print(json.dumps(evidence, sort_keys=True))
"""


def test_complete_source_construction_has_no_io() -> None:
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
    assert evidence["predicates"] == 12
    assert evidence["grains"] == 9
    assert evidence["aggregates"] == 8
    assert evidence["ties"] == 4
    assert evidence["guarded_negative_failures"] == 19
    assert evidence["guarded_entrypoints"] == 60
    assert evidence["checked_definitions"] == 57
    assert evidence["telemetry_enabled"] is True
    assert set(evidence["attempts"]) == {
        "source",
        "backend",
        "credentials",
        "metadata",
        "query",
        "compiler",
        "arrow",
        "parquet",
        "session",
        "store",
        "run",
        "artifact",
        "filesystem",
        "network",
    }
    assert all(count == 0 for count in evidence["attempts"].values())
