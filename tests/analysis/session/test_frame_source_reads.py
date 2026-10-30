"""Actual read identities survive fixed-data continuation without new source calls."""

from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timezone
from uuid import UUID

import pandas as pd
import pytest

import marivo.analysis as mv
from marivo.analysis.compiler.graph_lowering import LoweredRead
from marivo.analysis.compiler.graph_plan import SourceRead
from marivo.analysis.core.frame_data import MemoryFrameBacking
from marivo.analysis.core.frame_source_reads import (
    FrameSourceRead,
    FrameSourceReadPeriod,
    FrameSourceReadScope,
    merge_source_reads,
)
from marivo.analysis.core.model import CoreRuleError
from marivo.analysis.materialization import graph_source_execution
from marivo.analysis.materialization.graph_exchange import StageOutput
from marivo.analysis.materialization.graph_source_execution import PreparedSources
from marivo.datasource.adapters import CompiledRead, SourceSession
from tests.shared_fixtures import DslCaseFactory, analysis_dsl_rows
from tests.support.source_trace import capture_source

NOW = datetime(2026, 10, 30, tzinfo=timezone.utc)


def _reads(frame: mv.AnalysisFrame) -> tuple[FrameSourceRead, ...]:
    assert isinstance(frame._backing, MemoryFrameBacking)
    return frame._backing.metadata.source_reads


def test_actual_completion_ids_and_counts_survive_fixed_and_mixed_inputs(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = analysis_dsl_case_factory("j2")
    session = case.session
    pending = session.observe({"value": session.catalog.get("metric:sales.revenue")})
    trace = capture_source(monkeypatch)
    execute = SourceSession.execute
    returned_at: list[datetime] = []

    def witnessed(owner: SourceSession, read: CompiledRead) -> pd.DataFrame:
        result = execute(owner, read)
        returned_at.append(datetime.now(timezone.utc))
        return result

    monkeypatch.setattr(SourceSession, "execute", witnessed)
    before = datetime.now(timezone.utc)
    first = pending.compute()
    after = datetime.now(timezone.utc)
    original = _reads(first)
    expected = sum(row[-1] for row in analysis_dsl_rows("j2").orders)
    assert first.to_pandas()["value"].tolist() == [expected]
    assert [read.returned_rows for read in original] == trace.returned_rows
    assert len(original) == len(returned_at) == 1
    assert before <= returned_at[0] <= original[0].completed_at <= after
    assert UUID(original[0].read_id).hex == original[0].read_id

    second = pending.compute()
    independent = _reads(second)
    assert len(independent) == 1
    assert original[0].read_id != independent[0].read_id
    assert original[0].scopes == independent[0].scopes
    calls = len(trace.native_sql)
    derived = first.where(first["value"] > 0)
    derived = derived.select({"value": derived["value"]}).compute()
    assert _reads(derived) == original
    combined = first.compare({"reference": mv.baseline(second)}).compute()
    assert combined.to_pandas()["reference.absolute.value"].tolist() == [0]
    assert _reads(combined) == (*original, *independent)
    shared = first.compare({"reference": mv.baseline(derived)}).compute()
    assert _reads(shared) == original
    assert len(trace.native_sql) == calls
    mixed = first.compare({"reference": mv.baseline(pending)}).compute()
    assert _reads(mixed)[:1] == original
    assert len(_reads(mixed)) == 2
    assert _reads(mixed)[1].read_id not in {read.read_id for read in (*original, *independent)}
    assert len(trace.native_sql) == calls + 1
    saved = mixed.persist()
    assert saved.source_reads == _reads(mixed)
    assert len(trace.native_sql) == calls + 1


def test_missing_raw_count_does_not_fall_back_to_completed_row_count(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = analysis_dsl_case_factory("j2")
    read_stage = graph_source_execution.read_stage

    def unknown_count(
        prepared: PreparedSources,
        stage: SourceRead,
        lowered: LoweredRead,
        *,
        value_keys: tuple[str, ...] = (),
    ) -> StageOutput:
        return replace(
            read_stage(prepared, stage, lowered, value_keys=value_keys), source_rows=None
        )

    monkeypatch.setattr(graph_source_execution, "read_stage", unknown_count)
    saved = case.session.observe(
        {"value": case.session.catalog.get("metric:sales.revenue")}
    ).persist()
    assert len(saved.to_pandas()) == 1
    assert len(saved.source_reads) == 1
    assert saved.source_reads[0].returned_rows is None


def test_read_records_validate_closed_shapes_and_conflicting_inherited_ids() -> None:
    period = FrameSourceReadPeriod(None, NOW, "UTC")
    scope = FrameSourceReadScope("sales.orders", period)
    read = FrameSourceRead("read_exact", NOW, (scope,), 0)
    assert merge_source_reads((read,), (read,)) == (read,)
    assert merge_source_reads((read,), (replace(read, read_id="read_other"),)) == (
        read,
        replace(read, read_id="read_other"),
    )
    with pytest.raises(CoreRuleError, match="identical complete source-read"):
        merge_source_reads((read,), (replace(read, returned_rows=1),))
    with pytest.raises(CoreRuleError, match="nonnegative integer"):
        replace(read, returned_rows=True)
    with pytest.raises(CoreRuleError, match="UTC-aware"):
        replace(read, completed_at=NOW.replace(tzinfo=None))
    with pytest.raises(CoreRuleError, match="start before end"):
        replace(period, start=NOW)
    with pytest.raises(CoreRuleError, match="distinct typed"):
        replace(read, scopes=(scope, scope))
    with pytest.raises(FrozenInstanceError):
        read.returned_rows = 1  # type: ignore[misc]  # Acquired metadata is immutable.
    assert "FrameSourceRead" in repr(read) and ".show()" in repr(read)
    assert "\n" not in repr(read)
    assert "returned_rows: 0" in read.render()
    assert "start: None" in period.render()
    assert "period:" in scope.render()
