"""One current, atomically replaced Catalog projection."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from typing import Literal, TypedDict, cast
from uuid import uuid4

from marivo.server.catalog_projection import SUMMARY_FIELDS
from marivo.server.frames import directory, scope_cursor, unscoped_cursor
from marivo.server.wire import HELP, JsonObject, JsonValue, Limits, ServiceError, stale_cursor

Relation = Literal["dependencies", "dependents"]


class Entry(TypedDict):
    detail: JsonObject
    related: dict[Relation, list[str]]
    outside_scope: list[str]


def not_found() -> ServiceError:
    return ServiceError(
        "not_found",
        "The semantic object is absent from the current Catalog.",
        "id",
        "Select an exact canonical identity from GET /v1/semantic/objects.",
        "semantic.catalog",
    )


@dataclass(frozen=True)
class Snapshot:
    key: str
    loaded_at: str
    entries: dict[str, Entry]

    def objects(
        self, kind: str | None, domain: str | None, limit: int, cursor: str | None
    ) -> JsonObject:
        matches = [
            entry
            for entry in self.entries.values()
            if (kind is None or entry["detail"]["kind"] == kind)
            and (domain is None or entry["detail"]["domain"] == domain)
        ]
        scope = (self.key, "objects", kind, domain, limit)
        kinds = sorted({str(entry["detail"]["kind"]) for entry in self.entries.values()})
        domains = sorted(
            {
                value
                for entry in self.entries.values()
                if isinstance(value := entry["detail"]["domain"], str)
            }
        )
        return {
            **self._page(matches, limit, cursor, scope),
            "kinds": list(kinds),
            "domains": list(domains),
        }

    def detail(self, identity: str) -> JsonObject:
        entry = self.entries.get(identity)
        if entry is None:
            raise not_found()
        return entry["detail"]

    def related(
        self, identity: str, relation: Relation, limit: int, cursor: str | None
    ) -> JsonObject:
        entry = self.entries.get(identity)
        if entry is None:
            raise not_found()
        matches = [self.entries[key] for key in entry["related"][relation]]
        return {
            **self._page(matches, limit, cursor, (self.key, identity, relation, limit)),
            "outside_scope": list(entry["outside_scope"]) if relation == "dependencies" else [],
        }

    @staticmethod
    def _page(
        matches: list[Entry], limit: int, cursor: str | None, scope: tuple[object, ...]
    ) -> JsonObject:
        offset = 0
        if cursor is not None:
            position = unscoped_cursor(cursor, *scope)
            if type(position) is not int or not 0 < position < len(matches):
                raise stale_cursor()
            offset = position
        end = offset + limit
        items: list[JsonValue] = [
            {name: entry["detail"][name] for name in SUMMARY_FIELDS}
            for entry in matches[offset:end]
        ]
        return directory(items, limit, scope_cursor(end, *scope) if end < len(matches) else None)


class CatalogReads:
    def __init__(self, root: Path, limits: Limits) -> None:
        self.root = root
        self.limits = limits
        self.current: Snapshot | None = None
        self._task: asyncio.Task[Snapshot] | None = None

    async def get(self, *, reload: bool = False) -> Snapshot:
        if self.current is not None and not reload:
            return self.current
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._load())
            self._task.add_done_callback(_observe_failure)
        return await asyncio.shield(self._task)

    async def close(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _load(self) -> Snapshot:
        deadline = self.limits.deadline()
        try:
            payload = await asyncio.wait_for(self._run_worker(), deadline - monotonic())
            if monotonic() >= deadline:
                raise asyncio.TimeoutError
        except asyncio.TimeoutError:
            raise self.limits.timed_out("catalog") from None
        except (OSError, ValueError, KeyError):
            raise _load_error() from None
        if "diagnostics" in payload:
            raise _load_error(payload["diagnostics"])
        # The isolated worker is the same installed code and emits this closed
        # protocol after safe projection. No HTTP input is decoded as a snapshot.
        entries = cast("list[Entry]", payload["entries"])
        snapshot = Snapshot(
            uuid4().hex,
            datetime.now(timezone.utc).isoformat(),
            {str(entry["detail"]["id"]): entry for entry in entries},
        )
        if monotonic() >= deadline:
            raise self.limits.timed_out("catalog")
        self.current = snapshot
        return snapshot

    async def _run_worker(self) -> JsonObject:
        env = dict(os.environ)
        env.pop("MARIVO_API_TOKEN", None)
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            "import sys; sys.path.insert(0, sys.argv.pop(1)); "
            "import runpy; runpy.run_module('marivo.server.catalog_projection', run_name='__main__')",
            str(Path(__file__).resolve().parents[2]),
            str(self.root),
            str(self.limits.size),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=env,
        )
        try:
            assert process.stdout is not None
            data = bytearray()
            while chunk := await process.stdout.read(64 * 1024):
                data.extend(chunk)
                if len(data) > self.limits.size:
                    raise self.limits.too_large("catalog")
            status = await process.wait()
            if status == 3:
                raise self.limits.too_large("catalog")
            if status != 0:
                raise _load_error()
            payload: object = json.loads(data)
            if not isinstance(payload, dict) or not ({"entries", "diagnostics"} & payload.keys()):
                raise _load_error()
            return cast("JsonObject", payload)
        finally:
            if process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
                await process.communicate()


def _observe_failure(task: asyncio.Task[Snapshot]) -> None:
    if not task.cancelled():
        task.exception()


def _load_error(diagnostics: JsonValue = None) -> ServiceError:
    suffix = ""
    if isinstance(diagnostics, list) and diagnostics:
        suffix = " Diagnostic codes: " + ", ".join(str(item) for item in diagnostics[:10]) + "."
    return ServiceError(
        "catalog_load_failed",
        "Could not load the current Catalog." + suffix,
        "catalog",
        "Run marivo doctor --semantic in the bound project, fix the model and retry reload.",
        HELP,
    )
