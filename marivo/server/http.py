"""Authenticated ASGI adapter for the bounded project read service."""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import re
import sqlite3
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from time import monotonic
from urllib.parse import parse_qsl

from uvicorn._types import ASGIReceiveCallable, ASGISendCallable, Scope

from marivo import __version__
from marivo.analysis.errors import AnalysisError, ArtifactNotFoundError
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.result_files import ResultStorageError
from marivo.server import dataframes
from marivo.server.dataframes import dataframe_id
from marivo.server.frames import FrameReads
from marivo.server.protocol import ROUTES, Route, openapi
from marivo.server.wire import (
    API_VERSION,
    HELP,
    Budget,
    Limits,
    ServiceError,
    bad_request,
    stale_cursor,
)

_WORKERS = 4
_MAX_REQUESTS = 16
_METHODS = (b"GET", b"OPTIONS")
_HEADERS = {b"authorization", b"content-type"}


def unavailable() -> ServiceError:
    return ServiceError(
        "service_unavailable",
        "The service has no available read slot.",
        "service",
        "Retry when active reads finish.",
        HELP,
    )


def _query(route: Route, raw: bytes) -> dict[str, str]:
    pairs: list[tuple[str, str]] = []
    if raw:
        try:
            pairs = parse_qsl(
                raw.decode("ascii"),
                keep_blank_values=True,
                strict_parsing=True,
                errors="strict",
                max_num_fields=len(route.query),
            )
        except (ValueError, UnicodeError):
            raise bad_request(
                "query", "Invalid query parameters.", "Use the parameters declared in OpenAPI."
            ) from None
    values: dict[str, str] = {}
    for name, value in pairs:
        if (
            name not in route.query
            or name in values
            or (name != "cursor" and (not value or not value.isprintable()))
        ):
            raise bad_request(
                "query",
                "Unknown, repeated or invalid query parameter.",
                "Use each declared parameter at most once with a valid value.",
            )
        values[name] = value
    if "cursor" in values and (
        len(values["cursor"]) > 16384 or re.fullmatch(r"[A-Za-z0-9_-]+", values["cursor"]) is None
    ):
        raise stale_cursor()
    if "limit" in values and (
        re.fullmatch(r"[0-9]{1,3}", values["limit"]) is None or not 1 <= int(values["limit"]) <= 100
    ):
        raise bad_request(
            "limit",
            "Limit must be an integer from 1 through 100.",
            "Choose a smaller directory page size.",
        )
    return values


def _route(path: str, method: str) -> tuple[Route, dict[str, str]]:
    for route in ROUTES:
        parameters = route.match(path)
        if parameters is None or route.method != method:
            continue
        for name, value in parameters.items():
            if name == "result_id":
                dataframe_id(value)
            if (
                len(value.encode()) > 4096
                or not value.isprintable()
                or value != value.strip()
                or value in (".", "..")
            ):
                raise bad_request(
                    name, "Invalid identity.", "Use an exact identity from the directory."
                )
        return route, parameters
    if any(route.match(path) is not None for route in ROUTES):
        raise ServiceError(
            "method_not_allowed",
            "This resource is read-only.",
            "method",
            "Use GET for this resource.",
            HELP,
        )
    raise ServiceError(
        "not_found",
        "The requested route does not exist.",
        "path",
        "Use a route and method declared in GET /v2/openapi.json.",
        HELP,
    )


def _map_error(error: Exception, operation: str) -> ServiceError:
    if isinstance(error, ServiceError):
        return error
    if isinstance(error, ResultStorageError):
        return ServiceError(
            error.code,
            error.message,
            error.location or "result",
            error.repair.action if error.repair else "Check the saved result.",
            "analysis.persist_dataframe",
        )
    if isinstance(error, ArtifactNotFoundError):
        return ServiceError(
            "not_found",
            "The committed Frame is absent from this Session.",
            "artifact_id",
            "Check the project and the exact saved Frame reference.",
            "analysis.session.persisted_frame",
        )
    if isinstance(error, MaterializationError) and error.stage == "store_generation":
        return ServiceError(
            "unsupported_store_format",
            "The Store uses an unsupported format.",
            "store",
            "Use a compatible Marivo Runtime with the retained Store.",
            HELP,
        )
    if isinstance(error, (AnalysisError, sqlite3.DatabaseError, OSError)):
        return ServiceError(
            "corrupt_artifact",
            "The committed data or its Store metadata could not be read.",
            "store",
            "Restore the original committed files and metadata before retrying.",
            "analysis.session.persisted_frame",
        )
    logging.getLogger("uvicorn.error").error(
        "Read service failure during %s", operation, exc_info=error
    )
    return ServiceError(
        "internal_error",
        "The service could not complete the request.",
        "service",
        "Retain the reference and inspect the service logs.",
        HELP,
    )


async def _disconnect(receive: ASGIReceiveCallable) -> None:
    while (await receive())["type"] != "http.disconnect":
        pass


class ReadApplication:
    def __init__(
        self,
        root: Path,
        token: bytes,
        origins: frozenset[bytes],
        limits: Limits,
    ) -> None:
        self.root = root
        self.frames = FrameReads(root)
        self.token = token
        self.origins = origins
        self.limits = limits
        self.pool = ThreadPoolExecutor(max_workers=_WORKERS, thread_name_prefix="marivo-read")
        self.active_workers: set[Event] = set()
        self.active = 0
        self.spec = openapi()

    async def __call__(
        self,
        scope: Scope,
        receive: ASGIReceiveCallable,
        send: ASGISendCallable,
    ) -> None:
        if scope["type"] == "lifespan":
            await receive()
            await send({"type": "lifespan.startup.complete"})
            await receive()
            for event in self.active_workers:
                event.set()
            self.pool.shutdown(wait=False, cancel_futures=True)
            await send({"type": "lifespan.shutdown.complete"})
            return
        if scope["type"] != "http":
            return
        headers: dict[bytes, list[bytes]] = {}
        for name, value in scope["headers"]:
            headers.setdefault(name.lower(), []).append(value)
        origins = headers.get(b"origin", [])
        cors = [(b"vary", b"Origin")]
        allowed = len(origins) == 1 and origins[0] in self.origins
        if allowed:
            cors.append((b"access-control-allow-origin", origins[0]))
        preflight = scope["method"] == "OPTIONS"
        if preflight:
            cors[0] = (
                b"vary",
                b"Origin, Access-Control-Request-Method, Access-Control-Request-Headers",
            )
        operation = "request validation"
        try:
            if origins and not allowed:
                raise self._origin_error()
            if preflight:
                methods = headers.get(b"access-control-request-method", [])
                names = headers.get(b"access-control-request-headers", [])
                requested = {item.strip().lower() for value in names for item in value.split(b",")}
                if (
                    not allowed
                    or len(methods) != 1
                    or methods[0] not in _METHODS
                    or requested - _HEADERS
                ):
                    raise self._origin_error()
                cors.extend(
                    [
                        (b"access-control-allow-methods", b"GET, OPTIONS"),
                        (b"access-control-allow-headers", b"Authorization, Content-Type"),
                    ]
                )
                await self._respond(send, 204, b"", cors)
                return
            auth = headers.get(b"authorization", [])
            scheme, _, token = auth[0].partition(b" ") if len(auth) == 1 else (b"", b"", b"")
            if scheme.lower() != b"bearer" or not hmac.compare_digest(token, self.token):
                cors.append((b"www-authenticate", b"Bearer"))
                raise ServiceError(
                    "unauthorized",
                    "A valid Bearer token is required.",
                    "authorization",
                    "Use the token provided by the service administrator.",
                    HELP,
                )
            route, params = _route(scope["path"], scope["method"])
            operation = route.name
            query = _query(route, scope["query_string"])
            if self.active >= _MAX_REQUESTS:
                raise unavailable()
            self.active += 1
            try:
                result = await self._request(route, params, query, receive)
                if result is not None:
                    await self._respond(send, 200, result, cors)
            finally:
                self.active -= 1
        except Exception as error:
            failure = _map_error(error, operation)
            await self._respond(send, failure.status, json.dumps(failure.payload()).encode(), cors)

    @staticmethod
    def _origin_error() -> ServiceError:
        return ServiceError(
            "origin_not_allowed",
            "The cross-origin request is not allowed.",
            "origin",
            "Check the configured exact Origin and the allowed methods and headers.",
            HELP,
        )

    @staticmethod
    async def _respond(
        send: ASGISendCallable,
        status: int,
        body: bytes,
        headers: list[tuple[bytes, bytes]],
    ) -> None:
        response_headers = [*headers, (b"cache-control", b"no-store")]
        if status != 204:
            response_headers.extend(
                [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ]
            )
        await send({"type": "http.response.start", "status": status, "headers": response_headers})
        await send({"type": "http.response.body", "body": body})

    async def _request(
        self,
        route: Route,
        params: dict[str, str],
        query: dict[str, str],
        receive: ASGIReceiveCallable,
    ) -> bytes | None:
        location = (
            "frame"
            if route.name == "frame"
            else "directory"
            if "limit" in route.query
            else route.name
        )
        limits = self.limits
        deadline = limits.deadline()
        budget = Budget(limits, location, deadline, Event())
        work: asyncio.Task[bytes] | None = None
        disconnected: asyncio.Task[None] | None = None
        try:
            while True:
                event = await asyncio.wait_for(receive(), max(0, deadline - monotonic()))
                if event["type"] == "http.disconnect":
                    return None
                if event["type"] == "http.request":
                    if event.get("body"):
                        raise bad_request(
                            "body",
                            "This route accepts an empty request body.",
                            "Remove the request body and retry.",
                        )
                    if not event.get("more_body", False):
                        break
            work = asyncio.create_task(self._dispatch(route, params, query, budget))
            disconnected = asyncio.create_task(_disconnect(receive))
            done, _ = await asyncio.wait(
                (work, disconnected),
                timeout=max(0, deadline - monotonic()),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if work in done:
                return work.result()
            if disconnected in done:
                return None
            raise asyncio.TimeoutError
        except asyncio.TimeoutError:
            raise limits.timed_out(location) from None
        finally:
            budget.cancelled.set()
            tasks = [task for task in (work, disconnected) if task is not None]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _worker(self, budget: Budget, operation: Callable[[], bytes]) -> bytes:
        if len(self.active_workers) >= _WORKERS:
            raise unavailable()
        loop = asyncio.get_running_loop()
        self.active_workers.add(budget.cancelled)
        future = self.pool.submit(operation)

        def release() -> None:
            self.active_workers.discard(budget.cancelled)

        future.add_done_callback(
            lambda _: loop.call_soon_threadsafe(release) if not loop.is_closed() else None
        )
        return await asyncio.wrap_future(future)

    async def _dispatch(
        self,
        route: Route,
        params: dict[str, str],
        query: dict[str, str],
        budget: Budget,
    ) -> bytes:
        name = route.name
        limit = int(query.get("limit", "20"))
        cursor = query.get("cursor")
        session_id = params.get("session_id", "")
        if name == "dataframe":
            return await self._worker(
                budget, lambda: dataframes.report(self.root, params["result_id"], budget)
            )
        if name == "frame":
            return await self._worker(
                budget,
                lambda: self.frames.report(
                    session_id, params["artifact_id"], budget, store_id=params["store_id"]
                ),
            )
        if name == "sessions":
            return await self._worker(
                budget, lambda: budget.encode(self.frames.sessions(limit, cursor))
            )
        if name == "frames":
            return await self._worker(
                budget, lambda: budget.encode(self.frames.frames(session_id, limit, cursor))
            )
        if name == "openapi":
            return await self._worker(budget, lambda: budget.encode(self.spec))
        return await self._worker(
            budget,
            lambda: budget.encode(
                {
                    "api_version": API_VERSION,
                    "marivo_version": __version__,
                    "store_id": self.frames.store_id(),
                }
            ),
        )
