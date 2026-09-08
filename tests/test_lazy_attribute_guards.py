"""Actual Attribution worker admission, complete side guards, and failure atomicity."""

from __future__ import annotations

import time
from dataclasses import replace
from functools import partial
from pathlib import Path
from typing import Literal

import pyarrow as pa
import pytest

from marivo.analysis.materialization import admission
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.local import LocalPolicy
from marivo.analysis.materialization.local_worker import (
    LocalBoundary,
    LocalGraphRequest,
    LocalInputStreams,
    LocalPartInput,
    LocalStage,
    StreamInput,
    supervise,
)
from marivo.refs import ref
from tests.lazy_attribute_fixtures import REGION, inputs
from tests.lazy_local_fixtures import standalone_worker_reservation
from tests.lazy_materialization_crash_worker import snapshot
from tests.lazy_retained_fixtures import setup_retained

pytestmark = pytest.mark.runtime


def _worker(marker: Path) -> str:
    return (
        "from pathlib import Path\n"
        "import marivo.analysis.operators.attribute_values as attribution\n"
        "import marivo.analysis.materialization.attribution_publication as publication\n"
        "from marivo.analysis.materialization.local_worker import worker_entry\n"
        "original = attribution.execute_attribute\n"
        "def marked(*args, **kwargs):\n"
        f"    Path({str(marker)!r}).write_text('invoked')\n"
        "    return original(*args, **kwargs)\n"
        "attribution.execute_attribute = marked\n"
        "original_summary = publication.summarize_attribution_frame\n"
        "def marked_summary(*args, **kwargs):\n"
        f"    Path({str(marker.with_name('proof-invoked'))!r}).write_text('invoked')\n"
        "    return original_summary(*args, **kwargs)\n"
        "publication.summarize_attribution_frame = marked_summary\n"
        "worker_entry()\n"
    )


@pytest.mark.parametrize(
    "failure",
    [
        "schema",
        "foreign_key",
        "missing",
        "combined",
        "method",
        "intermediate",
        "output_rows",
        "output_bytes",
    ],
)
def test_complete_attribution_guards_precede_invocation_or_publication(
    tmp_path: Path, failure: str
) -> None:
    large = failure == "combined"
    frame, spec, parts = inputs(
        "revenue",
        [("a" * (10000 if large else 1),), ("b" * (10000 if large else 1),)],
        [(4.0, 1), (8.0, 1)],
        [(1.0, 1), (2.0, 1)],
    )
    primary = pa.Table.from_pandas(frame, preserve_index=False)
    part_tables = tuple(
        pa.Table.from_pandas(part.frame, schema=part.schema, preserve_index=False) for part in parts
    )
    selected = tuple(
        LocalPartInput(part.role, part.contract_id, part.contract_version, part.schema, part.keys)
        for part in parts
    )
    policy = LocalPolicy()
    if failure == "schema":
        baseline = part_tables[1]
        position = baseline.num_columns - 1
        changed = baseline.set_column(
            position, baseline.column_names[position], pa.array(["bad", "bad"])
        )
        part_tables = (part_tables[0], changed)
    elif failure == "foreign_key":
        changed = part_tables[1].set_column(0, "region", pa.array(["a", "foreign"]))
        part_tables = (part_tables[0], changed)
    elif failure == "missing":
        selected, part_tables = selected[:1], part_tables[:1]
    elif failure == "combined":
        policy = replace(policy, max_input_bytes=45_000)
        assert primary.nbytes + part_tables[0].nbytes < policy.max_input_bytes
        assert primary.nbytes + sum(table.nbytes for table in part_tables) > policy.max_input_bytes
    elif failure == "method":
        policy = replace(policy, max_method_rows=5)
    elif failure == "intermediate":
        policy = replace(policy, max_intermediate_bytes=8_000)
    elif failure == "output_rows":
        policy = replace(policy, max_output_rows=1)
    elif failure == "output_bytes":
        policy = replace(policy, max_output_bytes=1)
    request = LocalGraphRequest(
        (LocalBoundary(0, StreamInput(spec.input_row, spec.input_rows), selected),),
        (LocalStage(1, (0,), spec),),
        1,
        policy,
        time.monotonic() + 30,
    )
    marker = tmp_path / "attribute-invoked"
    terminated: list[bool] = []
    with pytest.raises(MaterializationError) as caught:
        supervise(
            request,
            (),
            cancel_source=lambda: None,
            lifetime=standalone_worker_reservation(tmp_path),
            terminal=lambda: terminated.append(True),
            input_streams=(
                LocalInputStreams(
                    primary.to_batches(), tuple(table.to_batches() for table in part_tables)
                ),
            ),
            worker_code=_worker(marker),
        )
    assert terminated == [True]
    assert marker.exists() == (failure == "output_bytes")
    assert not marker.with_name("proof-invoked").exists()
    assert caught.value.stage in ("transfer_guard", "output_validation")
    if failure == "combined":
        assert any(word in (caught.value.received or "") for word in ("combined", "conversion"))
    elif failure in ("method", "output_rows"):
        assert "method size" in (caught.value.received or "")
    elif failure == "intermediate":
        assert "intermediate allocation" in (caught.value.received or "")
    elif failure == "output_bytes":
        assert "output overflow" in (caught.value.received or "")


@pytest.mark.parametrize("mode", ["timeout", "exit"])
def test_failed_attribution_worker_publishes_no_artifact_and_releases_resources(
    tmp_path: Path, mode: Literal["timeout", "exit"], monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = setup_retained(tmp_path)
    runtime = fixture.runtime
    metric = (
        fixture.sources.observe(ref.metric("sales.revenue")).with_dimensions(REGION).aggregate()
    )
    delta = metric.compare(metric).execute()
    before = snapshot(runtime)
    fixture.database.rename(tmp_path / "warehouse.offline")
    runtime.local_policy = replace(LocalPolicy(), deadline_seconds=0.5 if mode == "timeout" else 5)
    code = "import time; time.sleep(30)" if mode == "timeout" else "import os; os._exit(3)"
    monkeypatch.setattr(admission, "supervise", partial(supervise, worker_code=code))
    started = time.monotonic()
    with pytest.raises(MaterializationError):
        delta.attribute(axes=(REGION,)).execute()
    assert time.monotonic() - started < 10
    after = snapshot(runtime)
    before_counts, after_counts = before["counts"], after["counts"]
    assert isinstance(before_counts, dict) and isinstance(after_counts, dict)
    for name in ("dataset_artifacts", "dataset_evidence", "findings"):
        assert after_counts[name] == before_counts[name]
    assert runtime.last_run_ref is not None
    run = runtime.store.run(runtime.last_run_ref)
    assert run is not None and run.lifecycle == "failed" and run.output_artifact_ref is None
    assert runtime.store.resources(runtime.session_ref) == ()
