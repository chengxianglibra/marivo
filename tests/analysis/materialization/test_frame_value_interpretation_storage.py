"""Cold current-result interpretation is independent of sources and parent outputs."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

import pytest

from tests.shared_fixtures import DslCaseFactory
from tests.support.paths import PROJECT_ROOT


@pytest.mark.slow
def test_interpretation_survives_producer_exit_without_models_source_or_parent(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    producer = """
import json, sys
from dataclasses import asdict
from pathlib import Path
import marivo.analysis as mv
assert Path(mv.__file__).resolve().parents[2] == Path(sys.argv[2])
session = mv.session.resume(sys.argv[1])
catalog = session.catalog
members = session.members(catalog.get('entity:sales.customer'))
days = session.time_scope(start='2026-08-01', end='2026-08-04').by(mv.grain('day'))
parent = session.observe(
    {'revenue': catalog.get('metric:sales.revenue'), 'orders': catalog.get('metric:sales.order_count')},
    by=mv.product(days, members),
).persist()
left = parent.select({
    'share': parent['revenue'].share(along=parent.axes),
    'coefficient': parent.over((parent['revenue'] * 2).corr(parent['orders'], method='pearson')),
    'pairs': parent.over(parent['orders'].pair_count(parent['revenue'] + 1)),
})
right = parent.select({'derived': parent['orders'] * 2, 'literal': parent['orders'] * 0 + 7})
assert set(left.schema.value_contexts) == {'share', 'coefficient', 'pairs'}, left.schema.value_contexts
saved = left.align(right).persist()
contexts = saved.schema.value_contexts
assert set(contexts) == {'left.share', 'left.coefficient', 'left.pairs'}, contexts
assert isinstance(contexts['left.share'], mv.FrameShareContext)
assert contexts['left.share'].numerator.name == 'revenue'
coefficient = contexts['left.coefficient']
assert isinstance(coefficient, mv.FramePairStatisticContext)
assert isinstance(coefficient.x, mv.FrameDerivedValueInput)
assert 'sales.revenue' in {o.path for o in coefficient.x.origins}
assert 'sales.order_count' not in {o.path for o in coefficient.x.origins}
assert coefficient.y.name == 'orders'
pairs = contexts['left.pairs']
assert pairs.x.name == 'orders'
assert isinstance(pairs.y, mv.FrameDerivedValueInput)
assert 'sales.revenue' in {o.path for o in pairs.y.origins}
assert saved.schema.alignments[0].right.values == ('right.derived', 'right.literal')
axis = next(a for a in saved.axes if a.role == 'calendar')
print(json.dumps({
    'artifact': saved.artifact_id,
    'parent': parent.artifact_id,
    'contexts': {name: asdict(context) for name, context in contexts.items()},
    'alignments': [asdict(a) for a in saved.schema.alignments],
    'periods': [asdict(p) for p in saved.read_periods(axis).items],
    'rows': saved.to_pandas().to_json(orient='split', date_format='iso'),
}, default=str, sort_keys=True))
"""
    produced = subprocess.run(
        [sys.executable, "-c", producer, case.session.id, str(PROJECT_ROOT)],
        cwd=case.root,
        env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)},
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert produced.returncode == 0, produced.stdout + produced.stderr
    expected = json.loads(produced.stdout)
    assert isinstance(expected, dict)
    case.database_path.rename(case.database_path.with_suffix(".offline"))
    (case.root / "models").rename(case.root / "models.offline")
    with case.session._runtime.store._write() as connection:
        connection.execute(
            "UPDATE dataset_artifacts SET descriptor_payload=? WHERE artifact_ref=?",
            ("unavailable parent output", expected["parent"]),
        )
    parent_directory = case.session._runtime.store.layout.artifact_dir(
        case.session.id, expected["parent"]
    )
    shutil.rmtree(parent_directory)
    assert not parent_directory.exists()
    expected_path = case.root / "interpretation-expected.json"
    expected_path.write_text(json.dumps(expected))
    reader = """
import json, sys
from dataclasses import asdict
from pathlib import Path
import marivo.analysis as mv
import marivo.semantic as ms
from marivo.datasource.adapters import SourceSession
from marivo.analysis.materialization.graph_reading import FrameReader, OutputRead
assert Path(mv.__file__).resolve().parents[2] == Path(sys.argv[3])
expected = json.loads(Path(sys.argv[2]).read_text())
def forbidden(*args, **kwargs):
    raise AssertionError('cold schema read touched source, rows, or method payloads')
ms.load = forbidden
SourceSession.__enter__ = forbidden
SourceSession.execute = forbidden
FrameReader.data = forbidden
FrameReader.method_facts = forbidden
open_payload = OutputRead._open
OutputRead._open = forbidden
session = mv.session.resume(sys.argv[1])
saved = session.persisted_frame(expected['artifact'])
schema = saved.schema
assert json.loads(json.dumps({name: asdict(c) for name, c in schema.value_contexts.items()}, default=str, sort_keys=True)) == expected['contexts']
assert json.loads(json.dumps([asdict(a) for a in schema.alignments], default=str, sort_keys=True)) == expected['alignments']
assert all(a.input_id == schema.input_id for a in schema.alignments)
schema.show()
saved.show(n=0)
OutputRead._open = open_payload
axis = next(a for a in saved.axes if a.role == 'calendar')
assert json.loads(json.dumps([asdict(p) for p in saved.read_periods(axis).items], default=str, sort_keys=True)) == expected['periods']
assert saved.read_page().to_pandas().to_json(orient='split', date_format='iso') == expected['rows']
print('cold-value-interpretation-ok')
"""
    restored = subprocess.run(
        [
            sys.executable,
            "-c",
            reader,
            case.session.id,
            str(expected_path),
            str(PROJECT_ROOT),
        ],
        cwd=case.root,
        env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert restored.returncode == 0, restored.stdout + restored.stderr
    assert "cold-value-interpretation-ok" in restored.stdout
