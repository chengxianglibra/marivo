"""Read committed project results using the same descriptors as Python."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from marivo.analysis.errors import SessionStateError
from marivo.analysis.session.read_spi import (
    CalendarComparisonBinding,
    CalendarDataAxis,
    DataType,
    FrameAxisSchema,
    FrameComparison,
    IntervalComparisonBinding,
    IntervalDataAxis,
    PairedDataAxis,
    StoredSession,
    axis_export_layout,
    comparison_id,
    decode_keyset_cursor,
    digest,
    encode_keyset_cursor,
    export_layout,
    find_sessions,
    frame_reference,
    session_page_after,
)
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


def frame_ref(store_id: str, session_id: str, artifact_id: str) -> JsonObject:
    result = fact(frame_reference(store_id, session_id, artifact_id), "ref")
    assert isinstance(result, dict)
    return result


def directory(items: list[JsonValue], limit: int, cursor: str | None) -> JsonObject:
    return {"items": items, "limit": limit, "has_more": cursor is not None, "next_cursor": cursor}


@dataclass(frozen=True)
class FrameReads:
    root: Path

    def store_id(self) -> str | None:
        store = find_sessions(self.root)
        return store.store_id if store is not None else None

    def sessions(self, limit: int, cursor: str | None) -> JsonObject:
        store = find_sessions(self.root)
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
                after = session_page_after(limit, inner)
            except SessionStateError:
                raise stale_cursor() from None
        page = store.recent(limit, after)
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

    def local(self, session_id: str, *, store_id: str | None = None) -> StoredSession:
        store = find_sessions(self.root)
        if store is not None and store_id is not None and store.store_id != store_id:
            raise ServiceError(
                "invalid_result_reference",
                "The result belongs to a different Store.",
                "store_id",
                "Use the service bound to the original Store.",
                "analysis.results.service.http",
            )
        selected = store.session(session_id) if store is not None else None
        if selected is None:
            raise ServiceError(
                "not_found",
                "The Session does not exist in this project's Store.",
                "session_id",
                "Check the project binding and select an ID from GET /v2/sessions.",
                "analysis.session.recent",
            )
        return selected

    def frames(self, session_id: str, limit: int, cursor: str | None) -> JsonObject:
        local = self.local(session_id)
        try:
            page = local.frames(limit, cursor)
        except SessionStateError:
            raise stale_cursor() from None
        return directory(
            [
                {
                    "ref": frame_ref(local.store_id, session_id, item.artifact_id),
                    "committed_at": item.committed_at.isoformat(),
                    "description": item.description,
                    "schema": fact(item.schema, "schema"),
                }
                for item in page.items
            ],
            limit,
            page.next_cursor,
        )

    def report(
        self, session_id: str, artifact_id: str, budget: Budget, *, store_id: str | None = None
    ) -> bytes:
        snapshot = self.local(session_id, store_id=store_id).frame(artifact_id)
        schema = snapshot.data_schema
        public = snapshot.schema
        metadata: JsonObject = {
            "kind": "analysis_frame",
            "ref": fact(snapshot.ref, "ref"),
            "description": snapshot.description,
            "committed_at": snapshot.committed_at.isoformat(),
            "schema": fact(public, "schema"),
            "lineage": fact(snapshot.lineage, "lineage"),
            "source_reads": fact(snapshot.source_reads, "source_reads"),
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
            page = snapshot.page(
                limit=_BATCH,
                cursor=cursor,
                axis_names=axis_names,
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
                page_periods = snapshot.periods(
                    target.value,
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
