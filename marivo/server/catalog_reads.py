"""One current, atomically replaced Catalog projection."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from typing import Literal, TypedDict, cast
from uuid import uuid4

from marivo.semantic.catalog_search import LexicalSearchConfig, SearchDocument, rank_documents
from marivo.server.frames import directory, scope_cursor, unscoped_cursor
from marivo.server.wire import HELP, JsonObject, JsonValue, Limits, ServiceError, stale_cursor

Relation = Literal["dependencies", "dependents"]
_SUMMARY_FIELDS = ("id", "kind", "path", "name", "domain", "business_definition")


class _EntryRequired(TypedDict):
    detail: JsonObject
    related: dict[Relation, list[str]]
    outside_scope: list[str]


class Entry(_EntryRequired, total=False):
    datasource_properties: JsonObject


def _search_document(detail: JsonObject) -> SearchDocument:
    # Only fields already disclosed by the HTTP detail can contribute to ranking.
    definition = detail["definition"]
    assert isinstance(definition, dict)
    context = definition["context"]
    assert isinstance(context, dict)
    aliases = context["aliases"]
    assert isinstance(aliases, list)
    domain = detail["domain"]
    business_definition = detail["business_definition"]
    return SearchDocument(
        id=str(detail["id"]),
        kind=str(detail["kind"]),
        path=str(detail["path"]),
        name=str(detail["name"]),
        domain=domain if isinstance(domain, str) else None,
        entity=None,
        fields=(
            (
                "business_definition",
                business_definition if isinstance(business_definition, str) else "",
            ),
            *(("alias", alias) for alias in aliases if isinstance(alias, str)),
        ),
    )


def not_found() -> ServiceError:
    return ServiceError(
        "not_found",
        "The semantic object is absent from the current Catalog.",
        "id",
        "Select an exact canonical identity from GET /v2/semantic/objects.",
        "semantic.catalog",
    )


def _page_offset(cursor: str | None, scope: tuple[object, ...]) -> int:
    if cursor is None:
        return 0
    offset = unscoped_cursor(cursor, *scope)
    if type(offset) is not int or offset < 1:
        raise stale_cursor()
    return offset


@dataclass(frozen=True)
class CatalogSnapshot:
    key: str
    loaded_at: str
    entries: dict[str, Entry]
    documents: tuple[SearchDocument, ...]
    root: Path

    def objects(
        self,
        kind: str | None,
        domain: str | None,
        limit: int,
        cursor: str | None,
        *,
        query: str | None,
    ) -> JsonObject:
        scope = (self.key, "objects", kind, domain, limit, query)
        offset = _page_offset(cursor, scope)
        documents = tuple(
            document
            for document in self.documents
            if (kind is None or document.kind == kind)
            and (domain is None or document.domain == domain)
        )
        if query is None:
            matches = [self.entries[document.id] for document in documents]
        else:
            hits = rank_documents(
                documents,
                query,
                workspace_dir=self.root,
                snapshot=self.key,
                config=LexicalSearchConfig(),
            )
            matches = [self.entries[hit.id] for hit in hits]
        kinds = sorted({document.kind for document in self.documents})
        domains = sorted(
            {document.domain for document in self.documents if document.domain is not None}
        )
        return {
            **self._page(matches, limit, offset, scope),
            "kinds": list(kinds),
            "domains": list(domains),
        }

    def detail(self, identity: str) -> JsonObject:
        entry = self.entries.get(identity)
        if entry is None:
            raise not_found()
        return entry["detail"]

    def datasource_properties(self, identity: str) -> JsonObject:
        entry = self.entries.get(identity)
        if entry is None or (properties := entry.get("datasource_properties")) is None:
            raise not_found()
        return properties

    def related(
        self, identity: str, relation: Relation, limit: int, cursor: str | None
    ) -> JsonObject:
        entry = self.entries.get(identity)
        if entry is None:
            raise not_found()
        scope = (self.key, identity, relation, limit)
        offset = _page_offset(cursor, scope)
        matches = [self.entries[key] for key in entry["related"][relation]]
        return {
            **self._page(matches, limit, offset, scope),
            "outside_scope": list(entry["outside_scope"]) if relation == "dependencies" else [],
        }

    @staticmethod
    def _page(
        matches: list[Entry], limit: int, offset: int, scope: tuple[object, ...]
    ) -> JsonObject:
        if offset and offset >= len(matches):
            raise stale_cursor()
        end = offset + limit
        items: list[JsonValue] = [
            {name: entry["detail"][name] for name in _SUMMARY_FIELDS}
            for entry in matches[offset:end]
        ]
        return directory(items, limit, scope_cursor(end, *scope) if end < len(matches) else None)


class CatalogReads:
    def __init__(self, root: Path, limits: Limits) -> None:
        self.root = root
        self.limits = limits
        self.current: CatalogSnapshot | None = None
        self._task: asyncio.Task[CatalogSnapshot] | None = None

    async def get(self, *, reload: bool = False) -> CatalogSnapshot:
        if self.current is not None and not reload:
            return self.current
        if reload and self._task is not None and not self._task.done():
            raise ServiceError(
                "catalog_load_in_progress",
                "A Catalog load is already in progress; no new reload was started.",
                "catalog",
                "Wait for the active load to finish, then request reload again.",
                HELP,
            )
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._load())
            self._task.add_done_callback(_observe_failure)
        return await asyncio.shield(self._task)

    async def close(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _load(self) -> CatalogSnapshot:
        deadline = self.limits.deadline()
        try:
            payload = await asyncio.wait_for(self._run_worker(), deadline - monotonic())
            if monotonic() >= deadline:
                raise asyncio.TimeoutError
        except asyncio.TimeoutError:
            raise self.limits.timed_out("catalog") from None
        except (OSError, ValueError, KeyError):
            logging.getLogger("uvicorn.error").exception("Catalog load failed")
            raise _load_error() from None
        if "diagnostics" in payload:
            raise _load_error(payload["diagnostics"])
        # The isolated worker is the same installed code and emits this closed
        # protocol after safe projection. No HTTP input is decoded as a snapshot.
        entries = cast("list[Entry]", payload["entries"])
        snapshot = CatalogSnapshot(
            uuid4().hex,
            datetime.now(timezone.utc).isoformat(),
            {str(entry["detail"]["id"]): entry for entry in entries},
            tuple(_search_document(entry["detail"]) for entry in entries),
            self.root,
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
            stderr=None,
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
                logging.getLogger("uvicorn.error").error(
                    "Catalog worker exited with status %s; inspect its stderr above", status
                )
                raise _load_error()
            payload: object = json.loads(data)
            if not isinstance(payload, dict) or not ({"entries", "diagnostics"} & payload.keys()):
                logging.getLogger("uvicorn.error").error(
                    "Catalog worker returned an invalid payload"
                )
                raise _load_error()
            return cast("JsonObject", payload)
        finally:
            if process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
                await process.communicate()


def _observe_failure(task: asyncio.Task[CatalogSnapshot]) -> None:
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
        "Inspect the service logs and run marivo doctor --semantic in the bound project; fix the model and retry reload.",
        HELP,
    )
