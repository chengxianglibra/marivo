"""Private canonical Run admission, sanitization, and compatibility projection."""

from __future__ import annotations

import json
import math
import re
import secrets
from collections.abc import Mapping, Sequence
from contextvars import ContextVar, Token
from dataclasses import dataclass, fields, is_dataclass
from datetime import date, datetime
from enum import Enum
from typing import TYPE_CHECKING, Literal, TypeAlias, cast

import numpy as np
from pydantic import BaseModel

from marivo._compat import UTC
from marivo.analysis.errors import AnalysisError, SessionStateError
from marivo.refs import Ref, RefPayloadV1

if TYPE_CHECKING:
    from marivo.analysis.session.core import Session

JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]

_MAX_DEPTH = 6
_MAX_COLLECTION_ITEMS = 64
_MAX_STRING_BYTES = 1024
_MAX_DOCUMENT_BYTES = 8192
_OMIT = object()
_SECRET_NAME_RE = re.compile(
    r"(?:password|passwd|secret|token|credential|api[_-]?key|authorization|cookie)", re.I
)
_SQL_NAME_RE = re.compile(r"(?:^|_)(?:sql|query|statement)(?:$|_)", re.I)
_SECRET_VALUE_RE = re.compile(
    r"(?i)(password|passwd|secret|token|api[_-]?key|authorization)\s*[:=]\s*([^\s,;]+)"
)
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_URL_USERINFO_RE = re.compile(r"(?i)([a-z][a-z0-9+.-]*://)[^/@\s]+@")
_SQL_TEXT_RE = re.compile(
    r"(?is)\b(?:select|insert|update|delete|merge|with)\b.{0,2048}"
    r"\b(?:from|into|set|using)\b"
)


def _bounded_string(value: str) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= _MAX_STRING_BYTES:
        return value
    return encoded[:_MAX_STRING_BYTES].decode("utf-8", errors="ignore") + "…"


def _sanitize_text(value: str) -> str:
    sanitized = _URL_USERINFO_RE.sub(r"\1<redacted>@", value)
    sanitized = _BEARER_RE.sub("Bearer <redacted>", sanitized)
    sanitized = _SECRET_VALUE_RE.sub(lambda match: f"{match.group(1)}=<redacted>", sanitized)
    if _SQL_TEXT_RE.search(sanitized):
        sanitized = "<redacted-sql>"
    return _bounded_string(sanitized)


def _contains_sensitive_text(value: str) -> bool:
    return any(
        pattern.search(value) is not None
        for pattern in (_SECRET_VALUE_RE, _BEARER_RE, _URL_USERINFO_RE, _SQL_TEXT_RE)
    )


def _normalize_value(
    value: object,
    *,
    depth: int,
    string_policy: Literal["plain", "omit-sensitive", "sanitize"] = "plain",
) -> JsonValue | object:
    if depth > _MAX_DEPTH:
        return _OMIT
    if value is None or isinstance(value, (str, bool, int)):
        if not isinstance(value, str):
            return value
        if string_policy == "omit-sensitive" and _contains_sensitive_text(value):
            return _OMIT
        if string_policy == "sanitize":
            return _sanitize_text(value)
        return _bounded_string(value)
    if isinstance(value, float):
        return value if math.isfinite(value) else _OMIT
    if isinstance(value, np.datetime64):
        return str(value)
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        normalized_float = float(value)
        return normalized_float if math.isfinite(normalized_float) else _OMIT
    if isinstance(value, Enum):
        return _normalize_value(
            value.value,
            depth=depth + 1,
            string_policy=string_policy,
        )
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Ref):
        return cast("JsonValue", RefPayloadV1.from_ref(value).to_dict())
    if isinstance(value, BaseModel):
        return _normalize_value(
            value.model_dump(mode="json"),
            depth=depth + 1,
            string_policy=string_policy,
        )
    if callable(value):
        return _OMIT
    if is_dataclass(value) and not isinstance(value, type):
        payload = {field.name: getattr(value, field.name) for field in fields(value)}
        return _normalize_value(
            payload,
            depth=depth + 1,
            string_policy=string_policy,
        )
    if isinstance(value, Mapping):
        if len(value) > _MAX_COLLECTION_ITEMS:
            return _OMIT
        if all(isinstance(key, str) for key in value):
            normalized: dict[str, JsonValue] = {}
            for key in sorted(cast("Sequence[str]", tuple(value.keys()))):
                if _SECRET_NAME_RE.search(key) or _SQL_NAME_RE.search(key):
                    return _OMIT
                item = _normalize_value(
                    value[key],
                    depth=depth + 1,
                    string_policy=string_policy,
                )
                if item is _OMIT:
                    return _OMIT
                normalized[key] = cast("JsonValue", item)
            return normalized
        entries: list[JsonValue] = []
        for key, item_value in value.items():
            normalized_key = _normalize_value(
                key,
                depth=depth + 1,
                string_policy=string_policy,
            )
            normalized_value = _normalize_value(
                item_value,
                depth=depth + 1,
                string_policy=string_policy,
            )
            if normalized_key is _OMIT or normalized_value is _OMIT:
                return _OMIT
            entries.append(
                {
                    "key": cast("JsonValue", normalized_key),
                    "value": cast("JsonValue", normalized_value),
                }
            )
        entries.sort(key=lambda item: json.dumps(item, sort_keys=True, ensure_ascii=False))
        return entries
    if isinstance(value, (list, tuple, set, frozenset)):
        if len(value) > _MAX_COLLECTION_ITEMS:
            return _OMIT
        normalized_items: list[JsonValue] = []
        for item_value in value:
            item = _normalize_value(
                item_value,
                depth=depth + 1,
                string_policy=string_policy,
            )
            if item is _OMIT:
                return _OMIT
            normalized_items.append(cast("JsonValue", item))
        if isinstance(value, (set, frozenset)):
            normalized_items.sort(
                key=lambda item: json.dumps(item, sort_keys=True, ensure_ascii=False)
            )
        return normalized_items
    return _OMIT


def project_run_arguments(
    arguments: Mapping[str, object],
) -> tuple[list[dict[str, object]], tuple[str, ...]]:
    """Return bounded safe arguments plus every explicitly omitted parameter name."""
    projected: list[dict[str, object]] = []
    omitted: list[str] = []
    for name in sorted(arguments):
        value = arguments[name]
        if _SECRET_NAME_RE.search(name) or _SQL_NAME_RE.search(name):
            omitted.append(name)
            continue
        normalized = _normalize_value(value, depth=0, string_policy="omit-sensitive")
        if normalized is _OMIT:
            omitted.append(name)
            continue
        candidate: dict[str, object] = {
            "name": name,
            "value": cast("JsonValue", normalized),
        }
        trial = [*projected, candidate]
        if len(json.dumps(trial, ensure_ascii=False).encode("utf-8")) > _MAX_DOCUMENT_BYTES:
            omitted.append(name)
            continue
        projected.append(candidate)
    return projected, tuple(omitted)


def collect_input_artifact_refs(arguments: Mapping[str, object]) -> tuple[str, ...]:
    """Collect Artifact inputs in public parameter and collection order."""
    from marivo.analysis.frames.base import BaseFrame

    refs: list[str] = []

    def visit(value: object) -> None:
        if isinstance(value, BaseFrame):
            # Only committed Artifact identities belong in the persisted Run
            # graph. In-memory frames used by lower-level/internal callers have
            # no Artifact identity and therefore no reverse-index edge.
            if value.meta.artifact_id is not None:
                refs.append(value.meta.artifact_id)
            return
        if isinstance(value, Mapping):
            for key, item in value.items():
                visit(key)
                visit(item)
            return
        if isinstance(value, (list, tuple)):
            for item in value:
                visit(item)

    for value in arguments.values():
        visit(value)
    # One Artifact may appear in more than one public parameter (for example,
    # comparing an Artifact with itself).  The Run graph stores normalized
    # dependency identities, not repeated call-site occurrences.
    return tuple(dict.fromkeys(refs))


def _generic_failure() -> dict[str, object]:
    return {
        "error_type": "InternalExecutionError",
        "message": "The admitted analysis execution failed; inspect the original raised error.",
        "expected": None,
        "received": None,
        "location": None,
        "repair": None,
    }


def project_run_failure(exc: Exception) -> dict[str, object]:
    """Project one ordinary exception into the closed safe Run failure shape."""
    if not isinstance(exc, AnalysisError):
        return _generic_failure()
    raw: dict[str, object] = {
        "error_type": type(exc).__name__,
        "message": _sanitize_text(exc.message),
        "expected": exc.expected,
        "received": exc.received,
        "location": exc.location,
        "repair": exc.repair.model_dump(mode="json") if exc.repair is not None else None,
    }
    normalized = _normalize_value(raw, depth=0, string_policy="sanitize")
    if normalized is _OMIT or not isinstance(normalized, dict):
        return _generic_failure()
    sanitized = cast("dict[str, object]", normalized)
    for field_name in ("expected", "received", "location"):
        value = sanitized.get(field_name)
        if isinstance(value, str):
            sanitized[field_name] = _sanitize_text(value)
    repair = sanitized.get("repair")
    if isinstance(repair, dict):
        for field_name in ("action", "snippet"):
            value = repair.get(field_name)
            if isinstance(value, str):
                repair[field_name] = _sanitize_text(value)
    if len(json.dumps(sanitized, ensure_ascii=False).encode("utf-8")) > _MAX_DOCUMENT_BYTES:
        return _generic_failure()
    return sanitized


@dataclass(slots=True)
class _RunAdmission:
    session: Session
    run_id: str
    capability_id: str
    _token: Token[_RunAdmission | None] | None = None
    _terminal_attempted: bool = False
    _completed: bool = False

    def __enter__(self) -> _RunAdmission:
        self._token = _ACTIVE_RUN.set(self)
        return self

    def succeed(
        self,
        output_artifact_ref: str,
        *,
        output_mode: str,
        finished_at: str | None = None,
        arguments: list[dict[str, object]] | None = None,
        omitted_argument_names: tuple[str, ...] | None = None,
    ) -> None:
        self._terminal_attempted = True
        self.session._store.complete_run(
            session_id=self.session.id,
            run_id=self.run_id,
            output_artifact_ref=output_artifact_ref,
            output_mode=output_mode,
            finished_at=finished_at or datetime.now(UTC).isoformat(),
            arguments=arguments,
            omitted_argument_names=omitted_argument_names,
        )
        self._completed = True

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> Literal[False]:
        try:
            if exc is None:
                if not self._completed:
                    raise SessionStateError(
                        message="materializing capability returned without completing its Run",
                        expected="one succeeded terminal Run transition",
                        received="incomplete",
                        location=f"Run {self.run_id!r}",
                    )
                return False
            if self._terminal_attempted or not isinstance(exc, Exception):
                return False
            try:
                self.session._store.fail_run(
                    session_id=self.session.id,
                    run_id=self.run_id,
                    failure=project_run_failure(exc),
                    failed_at=datetime.now(UTC).isoformat(),
                )
            except Exception as persistence_exc:
                raise SessionStateError(
                    message="failed to persist the admitted Run failure",
                    expected="one sanitized failed Run terminal record",
                    received=type(persistence_exc).__name__,
                    location=f"Run {self.run_id!r}",
                    context={"run_id": self.run_id},
                ) from exc
            return False
        finally:
            if self._token is not None:
                _ACTIVE_RUN.reset(self._token)


_ACTIVE_RUN: ContextVar[_RunAdmission | None] = ContextVar(
    "marivo_active_analysis_run", default=None
)


def admit_run(
    session: Session,
    *,
    capability_id: str,
    analysis_purpose: str | None,
    arguments: Mapping[str, object],
    input_artifact_refs: tuple[str, ...],
) -> _RunAdmission:
    """Persist one incomplete Run and return its execution context."""
    run_id = f"run_{secrets.token_hex(12)}"
    projected, omitted = project_run_arguments(arguments)
    session._store.begin_run(
        session_id=session.id,
        run_id=run_id,
        capability_id=capability_id,
        analysis_purpose=analysis_purpose,
        arguments=projected,
        omitted_argument_names=omitted,
        input_artifact_refs=input_artifact_refs,
        started_at=datetime.now(UTC).isoformat(),
    )
    return _RunAdmission(session=session, run_id=run_id, capability_id=capability_id)


def active_run_id() -> str | None:
    current = _ACTIVE_RUN.get()
    return current.run_id if current is not None else None


def active_run_admission() -> _RunAdmission | None:
    return _ACTIVE_RUN.get()


def require_active_run_id() -> str:
    run_id = active_run_id()
    if run_id is None:
        raise SessionStateError(
            message="materializing capability has no admitted Run",
            expected="an active private Run admission",
            received="none",
            location="analysis execution",
        )
    return run_id


def reconcile_incomplete_runs(session: Session) -> None:
    """Complete uniquely recoverable Runs before a persisted Session is activated."""
    import sqlite3
    from pathlib import Path

    from marivo.analysis.evidence.store import EXPECTED_SCHEMA_VERSION
    from marivo.analysis.frames.base import CURRENT_ARTIFACT_SCHEMA_VERSION
    from marivo.analysis.session._load import load_frame

    incomplete = [
        row for row in session._store.list_runs(session.id) if row["lifecycle"] == "incomplete"
    ]
    if not incomplete:
        return

    evidence_by_run: dict[str, list[str]] = {}
    judgment_path = session._layout.session_dir / "judgment.db"
    if judgment_path.is_file():
        uri = f"file:{judgment_path.as_posix()}?mode=ro"
        try:
            connection = sqlite3.connect(uri, uri=True)
            connection.row_factory = sqlite3.Row
            try:
                version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                if version != EXPECTED_SCHEMA_VERSION:
                    raise SessionStateError(
                        message="cannot reconcile Runs against an incompatible Evidence Store",
                        expected=f"judgment.db user_version={EXPECTED_SCHEMA_VERSION}",
                        received=str(version),
                        location=str(judgment_path),
                    )
                marker_rows = connection.execute(
                    "SELECT artifact_id FROM artifacts WHERE session_id = ?",
                    (session.id,),
                ).fetchall()
            finally:
                connection.close()
        except sqlite3.Error as exc:
            raise SessionStateError(
                message="cannot read the Evidence Store while reconciling incomplete Runs",
                expected="a readable exact-current judgment.db",
                received=type(exc).__name__,
                location=str(judgment_path),
            ) from exc
        for marker in marker_rows:
            artifact_id = str(marker["artifact_id"])
            meta_path = session._layout.frames_dir / artifact_id / "meta.json"
            try:
                payload = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            producer = payload.get("produced_by_job")
            kind = payload.get("kind")
            if (
                isinstance(producer, str)
                and kind not in {"component_frame", "coverage_frame"}
                and payload.get("artifact_schema_version") == CURRENT_ARTIFACT_SCHEMA_VERSION
                and Path(str(meta_path)).is_file()
            ):
                evidence_by_run.setdefault(producer, []).append(artifact_id)

    for run in incomplete:
        run_id = str(run["run_id"])
        # A Session Store registration cannot authorize recovery on its own:
        # only the canonical Evidence commit marker proves publication reached
        # the durable ledger boundary.
        candidates = sorted(set(evidence_by_run.get(run_id, ())))
        if not candidates:
            continue
        if len(candidates) != 1:
            raise SessionStateError(
                message="incomplete Run has multiple possible output Artifacts",
                expected="one unique producer match",
                received=str(candidates),
                location=f"Run {run_id!r} recovery",
            )
        artifact_ref = candidates[0]
        recovered = load_frame(
            artifact_ref,
            session=session,
            _require_evidence_marker=True,
        )
        if recovered.meta.session_id != session.id or recovered.meta.produced_by_job != run_id:
            raise SessionStateError(
                message="recovery Artifact ownership or producer does not match its Run",
                expected=f"Session {session.id!r}, producer {run_id!r}",
                received=(
                    f"Session {recovered.meta.session_id!r}, "
                    f"producer {recovered.meta.produced_by_job!r}"
                ),
                location=f"Artifact {artifact_ref!r} recovery",
            )
        refreshed = session._store.get_run(session.id, run_id)
        if refreshed is not None and refreshed["lifecycle"] == "incomplete":
            session._store.complete_run(
                session_id=session.id,
                run_id=run_id,
                output_artifact_ref=artifact_ref,
                output_mode="produced",
                finished_at=recovered.meta.created_at.isoformat(),
            )


__all__: list[str] = []
