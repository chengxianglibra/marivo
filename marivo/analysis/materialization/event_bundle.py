"""Read-only source Event packets and their action-owned stream."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import pyarrow as pa

from marivo.analysis.compiler.nodes import CompiledValidation

if TYPE_CHECKING:
    from marivo.analysis.materialization.scalar_sql_execution import ScalarExecutionAdapter


@dataclass(frozen=True, slots=True)
class EventBundleSQL:
    """One source statement with ordered validation, proof and primary packets."""

    sql: str
    validations: tuple[CompiledValidation, ...]
    primary_schema: pa.Schema
    proof_schema: pa.Schema


def _value(value: object, field: pa.Field) -> object:
    if value is None:
        return None
    if pa.types.is_struct(field.type):
        if not isinstance(value, dict) or set(value) != {child.name for child in field.type}:
            raise ValueError(f"invalid Event identity: {field.name}")
        return {child.name: _value(value[child.name], child) for child in field.type}
    if pa.types.is_timestamp(field.type):
        if not isinstance(value, str):
            raise ValueError(f"invalid Event timestamp: {field.name}")
        instant = datetime.fromisoformat(value)
        return instant.replace(tzinfo=timezone.utc) if instant.tzinfo is None else instant
    if pa.types.is_integer(field.type) and type(value) is not int:
        raise ValueError(f"invalid Event integer: {field.name}")
    return value


def _record(payload: object, schema: pa.Schema) -> dict[str, object]:
    if not isinstance(payload, str):
        raise ValueError("missing Event bundle payload")
    raw: object = json.loads(payload)
    if not isinstance(raw, dict) or set(raw) != set(schema.names):
        raise ValueError("Event bundle payload differs from its declared schema")
    return {field.name: _value(raw[field.name], field) for field in schema}


class EventBundleStream:
    """Validate source control packets before exposing complete journey rows."""

    def __init__(self, adapter: ScalarExecutionAdapter, bundle: EventBundleSQL) -> None:
        self._adapter = adapter
        self._schema = bundle.primary_schema
        self._closed = False
        self._cursor = adapter.cursor(stream=True)
        try:
            with adapter.submission("event_bundle", bundle.sql) as receipt:
                self._receipt = receipt
                self._cursor.execute(bundle.sql)
            accepted: list[tuple[str, int]] = []
            for index, check in enumerate(bundle.validations):
                rows = self._cursor.fetchmany(1)
                if len(rows) != 1 or rows[0] != (0, index, 0, None):
                    raise adapter.error(
                        check.expected or "zero Event source violations",
                        f"Event validation failed: {check.name}",
                        check.repair or "Repair the governed Event source rows.",
                        stage="output_validation",
                    )
                accepted.append((check.name, 0))
            proofs = self._cursor.fetchmany(1)
            if len(proofs) != 1 or proofs[0][:3] != (1, 0, None):
                raise ValueError("missing Event source proof packet")
            proof = _record(proofs[0][3], bundle.proof_schema)
            if proof.get("violations") != 0:
                raise ValueError("Event source output proof failed")
            self.proof = pa.Table.from_pylist([proof], schema=bundle.proof_schema)
            self.validations = (*accepted, ("event.journey.output", 0))
        except BaseException as error:
            if hasattr(self, "_receipt"):
                self._receipt.fail(error)
            with suppress(BaseException):
                self._cursor.close()
            raise

    @property
    def schema(self) -> pa.Schema:
        return self._schema

    def __iter__(self) -> Iterator[pa.RecordBatch]:
        if self._closed:
            raise self._adapter.error(
                "an open stream", "stream is closed", "Read each stream once."
            )
        try:
            while packets := self._cursor.fetchmany(1024):
                rows: list[dict[str, object]] = []
                for packet in packets:
                    if len(packet) != 4 or packet[0] != 2 or packet[2] is not None:
                        raise ValueError("invalid Event primary packet")
                    rows.append(_record(packet[3], self._schema))
                yield pa.RecordBatch.from_pylist(rows, schema=self._schema)
            self._adapter.validate_result()
        except BaseException as error:
            self._receipt.fail(error)
            with suppress(BaseException):
                self.close()
            raise
        self.close()

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            try:
                self._cursor.close()
            finally:
                self._adapter._streams.discard(self)
