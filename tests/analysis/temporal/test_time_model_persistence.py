"""Current temporal transport preserves captured authority without recovery guesses."""

import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest
from pydantic import BaseModel, TypeAdapter

from marivo._temporal import (
    Grain,
    TemporalSnapshotStore,
    _manifest_from_json,
    _snapshot_from_json,
    _snapshot_json,
    certify_period_calendar,
    semantic_grain,
)
from marivo._temporal_grid import BoundTimeGrid
from marivo.analysis.dsl.temporal import exact_scope
from marivo.refs import ref
from tests.support.paths import PROJECT_ROOT


def _snapshot():
    return certify_period_calendar(
        calendar_ref=ref.period_calendar("sales.fiscal"),
        boundary_timezone="UTC",
        coverage=(date(2026, 10, 1), date(2026, 10, 3)),
        rows=({"date": date(2026, 10, day), "period": "P1"} for day in (1, 2)),
        levels={"period": "period"},
    )


def test_grain_transport_retains_lookup_and_captured_identity():
    class Holder(BaseModel):
        grain: Grain

    snapshot = _snapshot()
    for authority in (None, snapshot):
        grain = semantic_grain(calendar=snapshot.calendar_ref, level="period", snapshot=authority)
        holder = Holder(grain=grain)
        restored = Holder.model_validate_json(holder.model_dump_json()).grain
        assert restored == grain and hash(restored) == hash(grain)
        assert restored._snapshot == authority
    with pytest.raises(ValueError, match="unsupported Grain schema"):
        Holder.model_validate({"grain": {"kind": "builtin", "unit": "day"}})
    with pytest.raises(ValueError, match="fields"):
        Holder.model_validate({"grain": {"schema": "grain/v2", "kind": "builtin", "unit": "day"}})


def test_retired_calendar_transport_is_rejected_without_modifying_old_file(tmp_path: Path):
    snapshot = _snapshot()
    store = TemporalSnapshotStore(tmp_path)
    store.publish(snapshot, definition_digest="current-definition")
    old = _snapshot_json(snapshot)
    old["schema"] = "period-calendar-snapshot/v1"
    old["correspondences"] = []
    path = tmp_path / "retired-calendar.json"
    path.write_text(json.dumps(old))
    before = path.read_bytes()
    with pytest.raises(ValueError, match=r"fields|schema"):
        _snapshot_from_json(json.loads(path.read_text()))
    with pytest.raises(ValueError, match="schema"):
        _manifest_from_json(
            {
                "schema": "period-calendar-manifest/v1",
                "calendar_ref": "sales.fiscal",
                "definition_digest": "old",
                "snapshot_digest": snapshot.snapshot_digest,
            }
        )
    assert path.read_bytes() == before
    assert (
        store.load_exact(snapshot.calendar_ref, snapshot_digest=snapshot.snapshot_digest)
        == snapshot
    )


def test_cold_grid_recovery_keeps_snapshot_and_actual_bucket_boundaries(tmp_path: Path):
    snapshot = _snapshot()
    grain = semantic_grain(calendar=snapshot.calendar_ref, level="period", snapshot=snapshot)
    grid = exact_scope("2026-10-01T12:00:00", "2026-10-02T12:00:00", "UTC").by(grain).grid
    adapter = TypeAdapter(BoundTimeGrid)
    path = tmp_path / "grid.json"
    path.write_bytes(adapter.dump_json(grid))
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
from pathlib import Path
import sys
from pydantic import TypeAdapter
from marivo._temporal_grid import BoundTimeGrid
value = TypeAdapter(BoundTimeGrid).validate_json(Path(sys.argv[1]).read_bytes(), strict=True)
assert value.calendar_snapshot == value.grain_spec._snapshot
assert value.cells[0].original_start.isoformat() == '2026-10-01T00:00:00+00:00'
assert value.cells[0].start.isoformat() == '2026-10-01T12:00:00+00:00'
assert value.cells[0].end.isoformat() == '2026-10-02T12:00:00+00:00'
print(value.identity)
""",
            str(path),
        ],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == grid.identity


def test_captured_grain_digest_enters_graph_and_deferred_fingerprints():
    from ibis.common.deferred import Just

    from marivo._definition.deferred import recipe_fingerprint
    from marivo._definition.graph import canonical_value
    from marivo._definition.windows import to_date

    snapshot = _snapshot()
    lookup = semantic_grain(calendar=snapshot.calendar_ref, level="period")
    captured = semantic_grain(calendar=snapshot.calendar_ref, level="period", snapshot=snapshot)
    assert canonical_value(lookup) != canonical_value(captured)
    assert recipe_fingerprint(Just(to_date(grain=lookup))) != recipe_fingerprint(
        Just(to_date(grain=captured))
    )
