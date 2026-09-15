"""Retained previews disclose duration units without collecting complete results."""

from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Literal

import pytest

from marivo.analysis.datasets.base import MaterializedDataset
from marivo.analysis.materialization import admission
from tests.lazy_adapter_runtime_worker import forbidden
from tests.lazy_event_runtime_fixtures import journey, setup_event
from tests.lazy_lifecycle_fixtures import history, setup_lifecycle
from tests.lazy_materialization_crash_worker import snapshot

pytestmark = pytest.mark.runtime


@pytest.mark.parametrize("family", ["event", "dwell"])
def test_duration_preview_discloses_units_under_read_bounds(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    family: Literal["event", "dwell"],
) -> None:
    materialized: MaterializedDataset
    if family == "event":
        runtime, sources, database = setup_event(tmp_path)
        materialized = journey(sources).execute()
    else:
        runtime, sources, database = setup_lifecycle(tmp_path)
        materialized = history(sources).dwell().execute()
        assert materialized.to_pandas().mean_duration.iloc[0] == timedelta(hours=3)
    duration_fields = tuple(
        field.name for field in materialized.schema.columns if field.logical_type_id == "duration"
    )
    assert duration_fields
    database.rename(tmp_path / "warehouse.offline")
    before = snapshot(runtime)
    monkeypatch.setattr(admission, "_build_backend_from_effective", forbidden)
    monkeypatch.setattr(admission, "_READ_POLICY", replace(admission._READ_POLICY, preview_rows=1))
    materialized.show()
    shown = capsys.readouterr().out
    assert "Durations (microseconds): " + ", ".join(duration_fields) in shown
    assert "Preview: 1 of " in shown
    if family == "dwell":
        assert "10800000000.0" in shown
    materialized.show(max_output_bytes=80)
    assert len(capsys.readouterr().out.encode()) <= 80
    materialized.to_pandas()
    assert snapshot(runtime) == before
