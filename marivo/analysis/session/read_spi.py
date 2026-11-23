"""Read committed Analysis state without activating a producing Session."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from marivo.analysis.core.frame_data import StoredFrameBacking
from marivo.analysis.dsl.frame import AnalysisFrame
from marivo.analysis.dsl.frame_factory import require_stored_frame
from marivo.analysis.dsl.frame_reopening import reopen_frame
from marivo.analysis.materialization.carriers.frame_page import (
    FrameCalendarPeriod,
    FrameComparisonPeriod,
    FrameIntervalPeriod,
    FramePage,
    FramePairedPeriod,
    FramePeriod,
    FramePeriodItem,
    FramePeriodPage,
)
from marivo.analysis.materialization.dataframe_store import _corrupt as corrupt_dataframe
from marivo.analysis.materialization.dataframe_store import dtype as dataframe_dtype
from marivo.analysis.materialization.dataframe_store import open_snapshot as open_dataframe_snapshot
from marivo.analysis.materialization.frame_paging import read_frame_page, read_frame_periods
from marivo.analysis.materialization.frame_services import LocalFrameServices
from marivo.analysis.materialization.layout import (
    MaterializationLayout,
)
from marivo.analysis.materialization.prior_generations import (
    discover_prior_generations,
    prior_generation_guidance,
)
from marivo.analysis.materialization.result_files import ResultErrorCode, ResultStorageError
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.model.data_schema import (
    CalendarComparisonBinding,
    CalendarDataAxis,
    DataSchema,
    IntervalComparisonBinding,
    IntervalDataAxis,
    PairedDataAxis,
)
from marivo.analysis.model.data_types import (
    DataType,
    DecimalDataType,
    DurationDataType,
    ScalarDataType,
    TimestampDataType,
)
from marivo.analysis.model.execution_errors import (
    MaterializationError,
)
from marivo.analysis.model.frame_facts import (
    FrameField,
)
from marivo.analysis.model.frame_schema import (
    CoordinateExportColumn,
    FieldExportColumn,
    FrameSchema,
    axis_export_layout,
    comparison_id,
    export_layout,
)
from marivo.analysis.model.frame_source_reads import (
    FrameSourceRead,
    FrameSourceReadPeriod,
    FrameSourceReadScope,
)
from marivo.analysis.model.layout_facts import (
    FrameAlignment,
    FrameAlignmentAxis,
    FrameAlignmentSide,
    FrameAxisGroup,
    FrameCalendarComparisonBinding,
    FrameChangeTarget,
    FrameComparison,
    FrameComparisonGroup,
    FrameComparisonPresence,
    FrameComparisonValue,
    FrameDecomposition,
    FrameDecompositionField,
    FrameDerivedValueInput,
    FrameFixedGroup,
    FrameIntervalComparisonBinding,
    FrameLevelTarget,
    FrameNamedValueInput,
    FrameOrdinaryComparisonBinding,
    FramePairStatisticContext,
    FrameShareContext,
    FrameStatisticContext,
)
from marivo.analysis.model.lineage import (
    ColumnLineage,
    FrameLineage,
    SemanticObjectIdentity,
)
from marivo.analysis.model.pages import decode_keyset_cursor, encode_keyset_cursor
from marivo.analysis.model.receipts import digest
from marivo.analysis.model.result_reference import (
    DataFrameReferenceWire,
    FrameReferenceWire,
    make_dataframe_reference,
    make_frame_reference,
)
from marivo.analysis.model.time_facts import (
    FrameAllHistoryWindow,
    FrameAxisSchema,
    FrameAxisTime,
    FrameBuiltinGrain,
    FrameCalendarAxisSchema,
    FrameCalendarEndpoint,
    FrameCivilDateSourceTime,
    FrameClockedContribution,
    FrameComparedTime,
    FrameContributionSampleWindowEndpoint,
    FrameDerivedTimeSemantics,
    FrameFieldSampleWindowEndpoint,
    FrameFixedAxis,
    FrameFixedTime,
    FrameIndependentContribution,
    FrameInstantSourceTime,
    FrameIntervalAxisSchema,
    FrameIntervalEndpoint,
    FrameObservationContext,
    FrameObservedTimeSemantics,
    FrameOrdinaryAxisSchema,
    FramePairedAxisSchema,
    FrameReportWindowEndpoint,
    FrameRowAttributeTime,
    FrameSinceWindow,
    FrameToDateWindow,
    FrameTrailingDurationWindow,
    FrameTrailingPeriodsWindow,
    FrameWallClockSourceTime,
)
from marivo.analysis.session._lazy_frames import persisted_frames
from marivo.analysis.session._lazy_history import recent
from marivo.analysis.session._lazy_read_model import (
    PersistedFrameSummaryPage,
    SessionSummary,
    SessionSummaryPage,
)
from marivo.analysis.session._lazy_runtime_reads import page_after as session_page_after


@dataclass(frozen=True, slots=True)
class FrameSnapshot:
    _frame: AnalysisFrame
    _backing: StoredFrameBacking
    _local: LocalFrameServices
    schema: FrameSchema

    @property
    def committed_at(self) -> datetime:
        return self._local.committed_at(self._backing)

    @property
    def data_schema(self) -> DataSchema:
        return self._backing.data.schema

    @property
    def ref(self) -> FrameReferenceWire:
        return self._frame.ref.to_dict()

    @property
    def description(self) -> str | None:
        return self._frame.description

    @property
    def lineage(self) -> FrameLineage:
        return self._frame.lineage

    @property
    def source_reads(self) -> tuple[FrameSourceRead, ...]:
        return self._frame.source_reads

    def page(
        self,
        *,
        limit: int,
        cursor: str | None,
        axis_names: tuple[str, ...],
        checkpoint: Callable[[], None],
    ) -> FramePage:
        return read_frame_page(
            input_id=self.schema.input_id,
            schema=self.data_schema,
            backing=self._backing,
            local=self._local,
            columns=None,
            limit=limit,
            cursor=cursor,
            axis_names=axis_names,
            time_details=True,
            checkpoint=checkpoint,
        )

    def periods(
        self,
        target: FrameAxisSchema | FrameComparison,
        *,
        limit: int,
        cursor: str | None,
        checkpoint: Callable[[], None],
    ) -> FramePeriodPage:
        return read_frame_periods(
            input_id=self.schema.input_id,
            schema=self.data_schema,
            backing=self._backing,
            local=self._local,
            target=target,
            limit=limit,
            cursor=cursor,
            checkpoint=checkpoint,
        )


@dataclass(frozen=True, slots=True)
class StoredSession:
    _local: LocalFrameServices

    @property
    def store_id(self) -> str:
        return self._local.store.store_id

    def frames(self, limit: int, cursor: str | None) -> PersistedFrameSummaryPage:
        return persisted_frames(self._local, limit=limit, cursor=cursor)

    def frame(self, artifact_id: str) -> FrameSnapshot:
        frame = reopen_frame(self._local, artifact_id)
        return FrameSnapshot(
            frame, require_stored_frame(frame, "detail"), self._local, frame.schema
        )


@dataclass(frozen=True, slots=True)
class StoredSessions:
    _store: SessionStore

    @property
    def store_id(self) -> str:
        return self._store.store_id

    def recent(self, limit: int, after: tuple[str, str] | None) -> SessionSummaryPage:
        return recent(self._store, limit=limit, after=after)

    def session(self, session_id: str) -> StoredSession | None:
        if self._store.session(session_id) is None:
            return None
        return StoredSession(LocalFrameServices(self._store, session_id))


def find_sessions(root: Path) -> StoredSessions | None:
    store = SessionStore.find_existing(root)
    return StoredSessions(store) if store is not None else None


def frame_reference(store_id: str, session_id: str, artifact_id: str) -> FrameReferenceWire:
    return make_frame_reference(store_id, session_id, artifact_id).to_dict()


def dataframe_reference(root: Path, result_id: str) -> DataFrameReferenceWire:
    return make_dataframe_reference(root, result_id).to_dict()


def check_existing_store(root: Path) -> None:
    SessionStore.open_existing(root)


__all__ = [
    "CalendarComparisonBinding",
    "CalendarDataAxis",
    "ColumnLineage",
    "CoordinateExportColumn",
    "DataType",
    "DecimalDataType",
    "DurationDataType",
    "FieldExportColumn",
    "FrameAlignment",
    "FrameAlignmentAxis",
    "FrameAlignmentSide",
    "FrameAllHistoryWindow",
    "FrameAxisGroup",
    "FrameAxisSchema",
    "FrameAxisTime",
    "FrameBuiltinGrain",
    "FrameCalendarAxisSchema",
    "FrameCalendarComparisonBinding",
    "FrameCalendarEndpoint",
    "FrameCalendarPeriod",
    "FrameChangeTarget",
    "FrameCivilDateSourceTime",
    "FrameClockedContribution",
    "FrameComparedTime",
    "FrameComparison",
    "FrameComparisonGroup",
    "FrameComparisonPeriod",
    "FrameComparisonPresence",
    "FrameComparisonValue",
    "FrameContributionSampleWindowEndpoint",
    "FrameDecomposition",
    "FrameDecompositionField",
    "FrameDerivedTimeSemantics",
    "FrameDerivedValueInput",
    "FrameField",
    "FrameFieldSampleWindowEndpoint",
    "FrameFixedAxis",
    "FrameFixedGroup",
    "FrameFixedTime",
    "FrameIndependentContribution",
    "FrameInstantSourceTime",
    "FrameIntervalAxisSchema",
    "FrameIntervalComparisonBinding",
    "FrameIntervalEndpoint",
    "FrameIntervalPeriod",
    "FrameLevelTarget",
    "FrameLineage",
    "FrameNamedValueInput",
    "FrameObservationContext",
    "FrameObservedTimeSemantics",
    "FrameOrdinaryAxisSchema",
    "FrameOrdinaryComparisonBinding",
    "FramePage",
    "FramePairStatisticContext",
    "FramePairedAxisSchema",
    "FramePairedPeriod",
    "FramePeriod",
    "FramePeriodItem",
    "FrameReportWindowEndpoint",
    "FrameRowAttributeTime",
    "FrameSchema",
    "FrameShareContext",
    "FrameSinceWindow",
    "FrameSourceRead",
    "FrameSourceReadPeriod",
    "FrameSourceReadScope",
    "FrameStatisticContext",
    "FrameToDateWindow",
    "FrameTrailingDurationWindow",
    "FrameTrailingPeriodsWindow",
    "FrameWallClockSourceTime",
    "IntervalComparisonBinding",
    "IntervalDataAxis",
    "MaterializationError",
    "MaterializationLayout",
    "PairedDataAxis",
    "ResultErrorCode",
    "ResultStorageError",
    "ScalarDataType",
    "SemanticObjectIdentity",
    "SessionSummary",
    "StoredSession",
    "TimestampDataType",
    "axis_export_layout",
    "check_existing_store",
    "comparison_id",
    "corrupt_dataframe",
    "dataframe_dtype",
    "dataframe_reference",
    "decode_keyset_cursor",
    "digest",
    "discover_prior_generations",
    "encode_keyset_cursor",
    "export_layout",
    "find_sessions",
    "frame_reference",
    "open_dataframe_snapshot",
    "prior_generation_guidance",
    "session_page_after",
]
