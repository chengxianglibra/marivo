"""Read committed project results using the same descriptors as Python."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from marivo.analysis._pages import decode_keyset_cursor, encode_keyset_cursor
from marivo.analysis.core.data_schema import (
    CalendarComparisonBinding,
    CalendarDataAxis,
    IntervalComparisonBinding,
    IntervalDataAxis,
    PairedDataAxis,
)
from marivo.analysis.core.data_types import DataType
from marivo.analysis.core.frame_interpretation import FrameAxisSchema, FrameComparison
from marivo.analysis.core.frame_schema import axis_export_layout, comparison_id, export_layout
from marivo.analysis.errors import SessionStateError
from marivo.analysis.materialization.contracts import digest
from marivo.analysis.materialization.frame_paging import read_frame_page, read_frame_periods
from marivo.analysis.materialization.frame_services import LocalFrameServices
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.session import _lazy_history
from marivo.analysis.session._lazy_frames import persisted_frames
from marivo.analysis.session._lazy_runtime_reads import page_after
from marivo.server.frame_projection import fact, page_rows, period, scalar
from marivo.server.wire import Budget, JsonObject, JsonValue, ServiceError, stale_cursor


def scope_cursor(cursor: str | int, *scope: object) -> str:
    return encode_keyset_cursor(cursor, digest(scope))


def unscoped_cursor(cursor: str, *scope: object) -> str | int:
    try:
        position, _ = decode_keyset_cursor(cursor)
        if scope_cursor(position, *scope) == cursor:
            return position
    except (ValueError, RecursionError):
        pass
    raise stale_cursor()


def frame_ref(session_id: str, artifact_id: str) -> JsonObject:
    return {"schema": "marivo.frame_ref/v1", "session_id": session_id, "artifact_id": artifact_id}


def directory(items: list[JsonValue], limit: int, cursor: str | None) -> JsonObject:
    return {"items": items, "limit": limit, "has_more": cursor is not None, "next_cursor": cursor}


@dataclass(frozen=True)
class FrameReads:
    root: Path

    def store_id(self) -> str | None:
        store = SessionStore.find_existing(self.root)
        return store.store_id if store is not None else None

    def sessions(self, limit: int, cursor: str | None) -> JsonObject:
        store = SessionStore.find_existing(self.root)
        if store is None:
            if cursor is not None:
                raise stale_cursor()
            return directory([], limit, None)
        scope = (store.store_id, "sessions", limit)
        after = None
        if cursor is not None:
            inner = unscoped_cursor(cursor, *scope)
            if not isinstance(inner, str):
                raise stale_cursor()
            try:
                after = page_after(limit, inner)
            except SessionStateError:
                raise stale_cursor() from None
        page = _lazy_history.recent(store, limit=limit, after=after)
        return directory(
            [
                {
                    "session_id": item.id,
                    "name": item.name,
                    "question": item.question,
                    "timezone": item.timezone,
                    "timezone_origin": item.timezone_origin,
                    "created_at": item.created_at.isoformat(),
                    "updated_at": item.updated_at.isoformat(),
                    "artifact_count": item.artifact_count,
                }
                for item in page.items
            ],
            limit,
            scope_cursor(page.next_cursor, *scope) if page.next_cursor is not None else None,
        )

    def local(self, session_id: str) -> LocalFrameServices:
        store = SessionStore.find_existing(self.root)
        if store is None or store.session(session_id) is None:
            raise ServiceError(
                "not_found",
                "The Session does not exist in this project's Store.",
                "session_id",
                "Check the project binding and select an ID from GET /v1/sessions.",
                "analysis.session.recent",
            )
        return LocalFrameServices(store, session_id)

    def frames(self, session_id: str, limit: int, cursor: str | None) -> JsonObject:
        local = self.local(session_id)
        try:
            page = persisted_frames(local, limit=limit, cursor=cursor)
        except SessionStateError:
            raise stale_cursor() from None
        return directory(
            [
                {
                    "ref": frame_ref(session_id, item.artifact_id),
                    "committed_at": item.committed_at.isoformat(),
                    "description": item.description,
                    "schema": fact(item.schema, "schema"),
                }
                for item in page.items
            ],
            limit,
            page.next_cursor,
        )

    def report(self, session_id: str, artifact_id: str, budget: Budget) -> bytes:
        local = self.local(session_id)
        frame = local.reopen(artifact_id)
        backing = frame._require_stored("detail")
        schema = backing.data.schema
        public = frame.schema
        metadata: JsonObject = {
            "kind": "analysis_frame",
            "ref": frame_ref(session_id, artifact_id),
            "description": frame.description,
            "committed_at": local.committed_at(backing).isoformat(),
            "schema": fact(public, "schema"),
            "lineage": fact(frame.lineage, "lineage"),
            "source_reads": fact(frame.source_reads, "source_reads"),
        }
        prefix = bytearray()
        budget.write(prefix, metadata)
        prefix.pop()
        budget.used -= 1
        budget.append(prefix, b',"data":{"layout":')

        taken = set(public.values) | set(public.presence)
        aliases = []
        for axis in schema.axes:
            base = axis.name.rsplit(".", 1)[-1]
            name = base
            suffix = 1
            while True:
                names = {
                    column.name for column in axis_export_layout(axis, name, time_details=True)
                }
                if names.isdisjoint(taken):
                    break
                suffix += 1
                name = f"{base}__{suffix}"
            taken.update(names)
            aliases.append(name)
        axis_names = tuple(aliases)
        layout = export_layout(schema, axis_names, time_details=True)
        budget.write(prefix, fact(layout, "data.layout"))
        budget.append(prefix, b',"rows":')
        rows = _Array(budget)
        coordinates = {slot: _Array(budget) for slot in schema.key_fields}
        cursor = None
        count = 0
        while True:
            budget.check()
            page = read_frame_page(
                input_id=public.input_id,
                schema=schema,
                backing=backing,
                local=local,
                columns=None,
                limit=_BATCH,
                cursor=cursor,
                axis_names=axis_names,
                time_details=True,
                checkpoint=budget.check,
            )
            for row in page_rows(page):
                rows.item(row)
            for slot, values in page.coordinates.items():
                for value in values:
                    coordinates[slot].item(scalar(value, f"data.coordinates.{slot}", exact=True))
            count += len(page)
            cursor = page.next_cursor
            if cursor is None:
                break

        parts = [prefix, rows.finish()]
        tail = bytearray()
        budget.append(tail, b',"coordinates":{')
        for index, (slot, values_array) in enumerate(coordinates.items()):
            if index:
                budget.append(tail, b",")
            budget.write(tail, slot)
            budget.append(tail, b":")
            parts.extend((tail, values_array.finish()))
            tail = bytearray()
        budget.append(tail, b'},"row_count":')
        budget.write(tail, count)
        budget.append(tail, b'},"periods":[')
        parts.append(tail)

        axes = {axis.token: axis for axis in schema.axes}
        targets: list[_PeriodTarget] = []
        for public_axis in public.axes:
            saved = axes[public_axis.token]
            if isinstance(saved, (CalendarDataAxis, IntervalDataAxis, PairedDataAxis)):
                targets.append(
                    _PeriodTarget(
                        public_axis,
                        {"kind": "axis", "axis_token": public_axis.token},
                        tuple(column.dtype for column in saved.columns),
                        tuple(column.dtype for column in saved.left.columns)
                        if isinstance(saved, PairedDataAxis)
                        else (),
                        tuple(column.dtype for column in saved.right.columns)
                        if isinstance(saved, PairedDataAxis)
                        else (),
                    )
                )
        comparisons = {comparison_id(item): item for item in schema.comparisons}
        for comparison in public.comparisons:
            binding = comparisons[comparison.comparison_id].binding
            if isinstance(binding, (CalendarComparisonBinding, IntervalComparisonBinding)):
                current = tuple(column.dtype for column in axes[binding.current_axis_token].columns)
                targets.append(
                    _PeriodTarget(
                        comparison,
                        {"kind": "comparison", "comparison_id": comparison.comparison_id},
                        current,
                        current,
                        tuple(column.dtype for column in binding.baseline.columns),
                    )
                )
        for index, target in enumerate(targets):
            header = bytearray()
            budget.append(header, b',{"target":' if index else b'{"target":')
            budget.write(header, target.identity)
            budget.append(header, b',"items":')
            parts.append(header)
            items = _Array(budget)
            cursor = None
            while True:
                budget.check()
                page_periods = read_frame_periods(
                    input_id=public.input_id,
                    schema=schema,
                    backing=backing,
                    local=local,
                    target=target.value,
                    limit=_BATCH,
                    cursor=cursor,
                    checkpoint=budget.check,
                )
                for item in page_periods.items:
                    items.item(period(item, target.dtypes, target.left, target.right))
                cursor = page_periods.next_cursor
                if cursor is None:
                    break
            end = bytearray()
            budget.append(end, b"}")
            parts.extend((items.finish(), end))

        end = bytearray()
        budget.append(end, b"]}")
        parts.append(end)
        body = b"".join(parts)
        budget.check()
        return body


_BATCH = 10_000


@dataclass(frozen=True)
class _PeriodTarget:
    value: FrameAxisSchema | FrameComparison
    identity: JsonObject
    dtypes: tuple[DataType, ...]
    left: tuple[DataType, ...]
    right: tuple[DataType, ...]


class _Array:
    """An ordered JSON fragment that charges every emitted byte to its report."""

    def __init__(self, budget: Budget) -> None:
        self.budget = budget
        self.data = bytearray()
        budget.append(self.data, b"[")

    def item(self, value: object) -> None:
        if len(self.data) > 1:
            self.budget.append(self.data, b",")
        self.budget.write(self.data, value)

    def finish(self) -> bytearray:
        self.budget.append(self.data, b"]")
        return self.data
