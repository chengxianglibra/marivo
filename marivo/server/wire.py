"""Bounded encoding and the service error contract."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from threading import Event
from time import monotonic
from typing import Literal, TypeAlias

JsonValue: TypeAlias = str | int | float | bool | list["JsonValue"] | dict[str, "JsonValue"] | None
JsonObject: TypeAlias = dict[str, JsonValue]
ErrorCode: TypeAlias = Literal[
    "invalid_parameter",
    "unauthorized",
    "origin_not_allowed",
    "not_found",
    "invalid_cursor",
    "response_too_large",
    "catalog_load_failed",
    "unsupported_store_format",
    "corrupt_artifact",
    "unencodable_value",
    "read_timeout",
    "service_unavailable",
    "internal_error",
]
API_VERSION = 1
DEFAULT_MIB = 128
DEFAULT_SECONDS = 300
HELP = "analysis.results.service"

STATUS: dict[ErrorCode, int] = {
    "invalid_parameter": 400,
    "unauthorized": 401,
    "origin_not_allowed": 403,
    "not_found": 404,
    "invalid_cursor": 409,
    "response_too_large": 413,
    "catalog_load_failed": 422,
    "unsupported_store_format": 422,
    "corrupt_artifact": 422,
    "unencodable_value": 422,
    "read_timeout": 503,
    "service_unavailable": 503,
    "internal_error": 500,
}


@dataclass(frozen=True)
class ServiceError(Exception):
    code: ErrorCode
    message: str
    location: str
    repair: str
    target: str | None = None

    @property
    def status(self) -> int:
        return STATUS[self.code]

    def payload(self) -> JsonObject:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "location": self.location,
                "repair": self.repair,
                "target": self.target,
            }
        }


def bad_request(location: str, message: str, repair: str) -> ServiceError:
    return ServiceError("invalid_parameter", message, location, repair, HELP)


def stale_cursor() -> ServiceError:
    return ServiceError(
        "invalid_cursor",
        "Cursor is invalid or belongs to another selection.",
        "cursor",
        "Start again without a cursor and repeat the same parameters on continuation.",
        HELP,
    )


@dataclass(frozen=True)
class Limits:
    mib: int = DEFAULT_MIB
    seconds: int = DEFAULT_SECONDS

    @property
    def size(self) -> int:
        return self.mib * 1024 * 1024

    def deadline(self) -> float:
        try:
            return monotonic() + self.seconds
        except OverflowError:
            return math.inf

    def too_large(self, location: str) -> ServiceError:
        repair = "Ask the administrator to increase --max-response-mib and retry."
        if location == "frame":
            repair = (
                "Ask the administrator to increase --max-response-mib and retry the same reference, "
                "or explicitly persist a smaller report result."
            )
        elif location == "directory":
            repair += " A smaller directory limit can also reduce this response."
        return ServiceError(
            "response_too_large",
            f"The complete response exceeds the configured {self.mib} MiB response limit.",
            location,
            repair,
            HELP,
        )

    def timed_out(self, location: str) -> ServiceError:
        repair = "Ask the administrator to increase --read-timeout-seconds and retry."
        if location == "frame":
            repair = (
                "Ask the administrator to increase --read-timeout-seconds and retry the same "
                "reference, or explicitly persist a smaller report result."
            )
        return ServiceError(
            "read_timeout",
            f"The read exceeded the configured {self.seconds} second time limit.",
            location,
            repair,
            HELP,
        )


class Budget:
    def __init__(
        self,
        limits: Limits,
        location: str,
        deadline: float,
        cancelled: Event,
    ) -> None:
        self.limits = limits
        self.location = location
        self.deadline = deadline
        self.cancelled = cancelled
        self.used = 0
        self.encoder = json.JSONEncoder(ensure_ascii=False, allow_nan=False, separators=(",", ":"))

    def check(self) -> None:
        if self.cancelled.is_set() or monotonic() >= self.deadline:
            raise self.limits.timed_out(self.location)

    def append(self, buffer: bytearray, part: bytes) -> None:
        self.used += len(part)
        if self.used > self.limits.size:
            raise self.limits.too_large(self.location)
        buffer.extend(part)

    def write(self, buffer: bytearray, value: object) -> None:
        self.check()
        for part in self.encoder.iterencode(value):
            self.append(buffer, part.encode("utf-8"))

    def encode(self, value: object) -> bytes:
        result = bytearray()
        self.write(result, value)
        payload = bytes(result)
        self.check()
        return payload
