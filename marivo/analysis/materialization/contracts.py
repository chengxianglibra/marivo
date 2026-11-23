"""Closed immutable v3 metadata and explicit, non-executable value codecs."""

from __future__ import annotations

import json
import math
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, TypeAlias, cast, get_args

from marivo._kernel.errors import HelpSurface, LiveHelpTarget
from marivo._kernel.render import Card, RenderableResult
from marivo.analysis.errors import AnalysisRepair, RepairKind
from marivo.analysis.model.receipts import FileEntry, LocalReceipt, _int, canonical_json, invalid


def parse_timestamp(value: str) -> datetime:
    """Decode an exact timezone-aware persisted timestamp without raw diagnostics."""
    result: datetime | None = None
    with suppress(ValueError):
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result is None or result.tzinfo is None or result.utcoffset() is None:
        raise invalid("invalid selected runtime timestamp")
    return result


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise invalid("duplicate JSON member")
        result[key] = value
    return result


def parse_json(text: str) -> object:
    try:
        value: object = json.loads(text, object_pairs_hook=_pairs)
        if canonical_json(value) != text:
            raise invalid("non-canonical JSON encoding")
        return value
    except (TypeError, ValueError, RecursionError):
        pass
    raise invalid("invalid JSON metadata")


def _obj(value: object, names: str) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != set(names.split()):
        raise invalid("unknown or missing metadata members")
    return {str(key): item for key, item in value.items()}


def _text(value: object, *, empty: bool = False) -> str:
    if type(value) is not str or (not empty and not value) or len(value.encode("utf-8")) > 4096:
        raise invalid("invalid bounded text")
    return value


def _array(value: object) -> tuple[object, ...]:
    if type(value) is not list:
        raise invalid("invalid array")
    return tuple(value)


def decode_receipt(value: object) -> LocalReceipt:
    obj = _obj(
        value,
        "kind project_relative_path format parquet_contract_version file_manifest realized_row_count realized_byte_count",
    )
    if obj["kind"] != "local" or obj["format"] != "parquet":
        raise invalid("unsupported storage receipt")
    entries = []
    for item in _array(obj["file_manifest"]):
        entry = _obj(item, "relative_path size_bytes")
        entries.append(FileEntry(_text(entry["relative_path"]), _int(entry["size_bytes"])))
    byte_count = _obj(obj["realized_byte_count"], "kind byte_count")
    if byte_count["kind"] != "exact":
        raise invalid("local receipt requires exact bytes")
    return LocalReceipt(
        _text(obj["project_relative_path"]),
        tuple(entries),
        _int(obj["realized_row_count"]),
        _int(byte_count["byte_count"]),
        _int(obj["parquet_contract_version"], minimum=1),
    )


JsonValue: TypeAlias = str | int | float | bool | list["JsonValue"] | dict[str, "JsonValue"] | None
RunFailurePhase: TypeAlias = Literal[
    "authority_resolution",
    "semantic_validation",
    "graph_validation",
    "implementation_registration",
    "execution_boundary",
    "source_binding",
    "ibis_expression_construction",
    "storage_selection",
    "ibis_backend_compile",
    "stage_execution",
    "transfer_guard",
    "output_validation",
    "storage_staging",
    "storage_finalization",
    "quality",
    "publication",
    "cleanup",
    "presentation",
    "process_lost",
]


@dataclass(frozen=True, slots=True, repr=False, init=False)
class RunFailure(RenderableResult):
    phase: RunFailurePhase
    kind: str
    safe_message: str
    safe_location: str | None
    _expected_json: str
    _received_json: str
    repair: AnalysisRepair | None
    backend_class: str | None
    retry_disposition: Literal["retryable", "not_retryable"]

    def __init__(
        self,
        phase: RunFailurePhase,
        kind: str,
        safe_message: str,
        safe_location: str | None,
        expected: JsonValue,
        received: JsonValue,
        repair: AnalysisRepair | None,
        backend_class: str | None = None,
        retry_disposition: Literal["retryable", "not_retryable"] = "retryable",
    ) -> None:
        object.__setattr__(self, "phase", phase)
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "safe_message", safe_message)
        object.__setattr__(self, "safe_location", safe_location)
        object.__setattr__(self, "_expected_json", canonical_json(_failure_json(expected)))
        object.__setattr__(self, "_received_json", canonical_json(_failure_json(received)))
        object.__setattr__(self, "repair", repair)
        object.__setattr__(self, "backend_class", backend_class)
        object.__setattr__(self, "retry_disposition", retry_disposition)
        self.__post_init__()

    @property
    def expected(self) -> JsonValue:
        """Return an isolated JSON projection of the immutable expected facts."""
        return _failure_json(parse_json(self._expected_json))

    @property
    def received(self) -> JsonValue:
        """Return an isolated JSON projection of the immutable received facts."""
        return _failure_json(parse_json(self._received_json))

    def __post_init__(self) -> None:
        if self.phase not in _PHASES or self.retry_disposition not in (
            "retryable",
            "not_retryable",
        ):
            raise invalid("unsupported Run failure variant")
        if self.kind not in ("execution_failed", "process_lost") or self.backend_class is not None:
            raise invalid("unregistered Run failure kind or backend class")
        for value in (self.kind, self.safe_message):
            _text(value)
        for optional_value in (self.safe_location, self.backend_class):
            if optional_value is not None:
                _text(optional_value)
        _failure_json(self.expected)
        _failure_json(self.received)
        if self.repair is not None and type(self.repair) is not AnalysisRepair:
            raise invalid("Run repair must be an exact AnalysisRepair")
        repair_payload = None if self.repair is None else self.repair.model_dump(mode="json")
        if len(canonical_json((self.expected, self.received, repair_payload)).encode()) > 8192:
            raise invalid("Run failure diagnostic byte bound exceeded")

    def _repr_identity(self) -> str:
        return f"RunFailure phase={self.phase} kind={self.kind}"[:256]

    def _card(self) -> Card:
        card = (
            Card(identity=self._repr_identity(), available=(".show()",))
            .field("message", self.safe_message)
            .field("retry_disposition", self.retry_disposition)
        )
        if self.repair is not None:
            card.field("repair", self.repair.action)
        return card


_PHASES: frozenset[str] = frozenset(get_args(RunFailurePhase))


def run_failure_phase(stage: str, owning_phase: str) -> RunFailurePhase:
    """Project a reader/action error stage onto the closed persisted Run phases."""
    if owning_phase not in _PHASES:
        raise invalid("unsupported owning Run phase")
    # Both alternatives have been checked against the closed phase registry.
    return cast("RunFailurePhase", stage if stage in _PHASES else owning_phase)


def _failure_json(value: object, *, depth: int = 0) -> JsonValue:
    if depth > 6:
        raise invalid("Run failure JSON depth exceeded")
    if value is None or type(value) is bool or type(value) is int:
        return value
    if isinstance(value, str):
        return _text(value, empty=True)
    if isinstance(value, float) and math.isfinite(value):
        return value
    if isinstance(value, list) and len(value) <= 64:
        return [_failure_json(item, depth=depth + 1) for item in value]
    if isinstance(value, dict) and len(value) <= 64:
        return {_text(key): _failure_json(item, depth=depth + 1) for key, item in value.items()}
    raise invalid("unsupported bounded Run failure JSON")


def failure_payload(value: RunFailure) -> dict[str, object]:
    return {
        "phase": value.phase,
        "kind": value.kind,
        "safe_message": value.safe_message,
        "safe_location": value.safe_location,
        "expected": value.expected,
        "received": value.received,
        "repair": None if value.repair is None else value.repair.model_dump(mode="json"),
        "backend_class": value.backend_class,
        "retry_disposition": value.retry_disposition,
    }


def _decode_repair(value: object) -> AnalysisRepair:
    obj = _obj(value, "kind action help_target snippet candidates")
    _failure_json(obj)
    target = obj["help_target"]
    if not isinstance(target, dict) or set(target) not in (
        {"surface"},
        {"surface", "canonical_id"},
    ):
        raise invalid("invalid structured Run repair")
    kind = obj["kind"]
    surface: object = target["surface"]
    if kind not in get_args(RepairKind) or surface not in get_args(HelpSurface):
        raise invalid("invalid structured Run repair")
    canonical_id: object = target.get("canonical_id")
    snippet = obj["snippet"]
    return AnalysisRepair(
        kind=cast("RepairKind", kind),  # Checked against the closed kinds above.
        action=_text(obj["action"], empty=True),
        help_target=LiveHelpTarget(
            surface=cast("HelpSurface", surface),  # Checked against the closed surfaces above.
            canonical_id=None if canonical_id is None else _text(canonical_id, empty=True),
        ),
        snippet=None if snippet is None else _text(snippet, empty=True),
        candidates=tuple(_text(item, empty=True) for item in _array(obj["candidates"])),
    )


def decode_failure(text: str) -> RunFailure:
    obj = _obj(
        parse_json(text),
        "phase kind safe_message safe_location expected received repair backend_class retry_disposition",
    )
    phase = _text(obj["phase"])
    disposition = obj["retry_disposition"]
    if phase not in _PHASES or disposition not in ("retryable", "not_retryable"):
        raise invalid("unsupported failure variant")
    repair = None if obj["repair"] is None else _decode_repair(obj["repair"])
    if len(canonical_json((obj["expected"], obj["received"], obj["repair"])).encode()) > 8192:
        raise invalid("Run failure diagnostic byte bound exceeded")
    return RunFailure(
        run_failure_phase(phase, phase),
        _text(obj["kind"]),
        _text(obj["safe_message"]),
        None if obj["safe_location"] is None else _text(obj["safe_location"]),
        _failure_json(obj["expected"]),
        _failure_json(obj["received"]),
        repair,
        None if obj["backend_class"] is None else _text(obj["backend_class"]),
        "retryable" if disposition == "retryable" else "not_retryable",
    )


@dataclass(frozen=True, slots=True)
class SessionRecord:
    session_ref: str
    name: str
    question: str | None
    report_timezone_name: str
    report_timezone_resolution: Literal["iana", "fixed_offset"]
    report_timezone_origin: Literal["explicit", "system"]
    created_at: str
    updated_at: str

    domains: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True)
class ResourceRecord:
    run_ref: str
    resource_kind: str
    execution_domain_id: str
    ownership_nonce: str
    cleanup_capability_id: str
    safe_locator: str
