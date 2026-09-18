"""Fresh-process acceptance of the private Dataset Core no-I/O boundary."""

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

from marivo.analysis.materialization.admission import DatasetRuntime
import marivo.datasource.backends as backends
import marivo.analysis.datasets.fields
import marivo.analysis.datasets.contract
from marivo.analysis.session.core import Session
from marivo.analysis.materialization.store import SessionStore
from marivo.datasource.runtime import DatasourceConnectionService
from marivo.analysis.datasets.errors import DatasetConstructionError
from tests.lazy_dataset_fixtures import (
    make_logical_dataset, make_materialized_dataset, make_owner, make_test_registry,
)

# Import existing package infrastructure before observing the pure Core journey.
# No Session, backend, Store or Artifact is constructed during bootstrap.
attempts = dict(datasource=0, connection=0, query=0, run=0, artifact=0,
                store=0, evidence=0, binding=0, filesystem=0, network=0)
active = False

def reject(kind):
    def blocked(*args, **kwargs):
        attempts[kind] += 1
        raise AssertionError('Core attempted ' + kind)
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
    (Session, '__init__', 'store'),
    (Session, 'observe', 'datasource'),
    (Session, 'source_bindings', 'binding'),
    (Session, 'artifact', 'artifact'),
    (SessionStore, '__init__', 'store'),
    (SessionStore, '_connection', 'store'),
    (SessionStore, 'admit', 'run'),
    (SessionStore, 'publish', 'artifact'),
    (SessionStore, 'lookup', 'binding'),
    (DatasetRuntime, 'execute_metric', 'run'),
    (DatasetRuntime, 'execute_population', 'run'),
    (DatasetRuntime, '_observe_submission', 'query'),
)
with ExitStack() as stack:
    for owner, name, kind in guards:
        stack.enter_context(patch.object(owner, name, reject(kind)))
    active = True
    owner = make_owner()
    registry = make_test_registry()
    root = make_logical_dataset(owner=owner, registry=registry)
    tip = root
    for _ in range(2000):
        tip = make_logical_dataset(owner=owner, registry=registry,
                                   operation_id='test.step', inputs=(tip,))
    scan = make_materialized_dataset(owner=owner, registry=registry)
    scan_branch = make_logical_dataset(owner=owner, registry=registry,
                                      operation_id='test.step', inputs=(scan,))
    negative_failures = 0
    for invalid in (
        lambda: make_logical_dataset(parameters=(float('nan'),)),
        lambda: make_logical_dataset(owner=make_owner(session_id='foreign'),
                    registry=registry, operation_id='test.step', inputs=(root,)),
        lambda: setattr(root, 'kind', 'changed'),
    ):
        try:
            invalid()
        except DatasetConstructionError as error:
            assert error.expected and error.received and error.location and error.hint
            assert '0x' not in str(error)
            negative_failures += 1
        else:
            raise AssertionError('Invalid pure construction was admitted')
    assert negative_failures == 3
    for value in (root, tip, scan, scan_branch):
        assert value.schema is value.row_contract.schema
        assert value.fields.get('value').field_id.value == 'value'
        assert len(repr(value)) <= 200
        assert len(value.contract().render().encode()) <= 8192
    assert tip.state.kind == scan_branch.state.kind == 'logical'
    assert tip.row_contract == root.row_contract
    assert tip.row_set_contract == root.row_set_contract
    assert root.definition_fingerprint != tip.definition_fingerprint
    assert len(tip._lineage.facts) == 16
    assert tip._lineage.omitted_count == 1985
    assert not any(attempts.values()), attempts
    result = dict(session_id=owner.session_id, logical_nodes=2001,
                  first_fingerprint=root.definition_fingerprint,
                  final_fingerprint=tip.definition_fingerprint,
                  lineage_count=len(tip._lineage.facts),
                  lineage_omitted=tip._lineage.omitted_count,
                  guarded_negative_failures=negative_failures,
                  guarded_entrypoints=len(guards), attempts=attempts)
    active = False
print(json.dumps(result, sort_keys=True))
"""


def test_deep_private_dataset_dag_is_pure() -> None:
    result = subprocess.run(
        [sys.executable, "-B", "-c", _JOURNEY],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    evidence = json.loads(result.stdout)
    assert evidence["logical_nodes"] == 2001
    assert evidence["guarded_entrypoints"] == 17
    assert evidence["lineage_count"] == 16
    assert evidence["lineage_omitted"] == 1985
    assert evidence["guarded_negative_failures"] == 3
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
    print("Dataset Core no-I/O acceptance: " + result.stdout.strip())
