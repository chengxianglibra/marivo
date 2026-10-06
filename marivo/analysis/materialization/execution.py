"""Private immutable execution inputs and action-local execution ownership."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import suppress
from dataclasses import dataclass
from typing import Protocol

import pyarrow as pa

from marivo.analysis.datasets.descriptors import DatasetRowContract, DatasetRowSetContract
from marivo.analysis.materialization.contracts import ExchangeBinding
from marivo.analysis.materialization.errors import MaterializationError


class BatchStream(Protocol):
    @property
    def schema(self) -> pa.Schema: ...
    def __iter__(self) -> Iterator[pa.RecordBatch]: ...
    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class ExchangeStreamBinding:
    """Closed physical stream contract for a relation or a Cell-valued result."""

    kind: str
    row: DatasetRowContract
    rows: DatasetRowSetContract
    schema: pa.Schema
    cell_reasons: tuple[tuple[str, tuple[str, ...]], ...] = ()

    def __post_init__(self) -> None:
        from marivo.analysis.materialization.storage import _matches_type

        if self.kind not in ("relation", "value"):
            raise _exchange_error("unknown exchange kind")
        names = tuple(self.schema.names)
        if names != tuple(field.name for field in self.row.schema.columns) or any(
            field.nullable != arrow.nullable or not _matches_type(field.logical_type_id, arrow.type)
            for field, arrow in zip(self.row.schema.columns, self.schema, strict=True)
        ):
            raise _exchange_error("stream fields differ from row contract")
        has_cells = names[-3:] == ("value", "cell_tag", "cell_reason")
        if has_cells != (self.kind == "value"):
            raise _exchange_error("relation and Cell fields differ")
        if self.kind == "relation" and self.cell_reasons:
            raise _exchange_error("relation has Cell reasons")


def _exchange_error(received: str) -> MaterializationError:
    return MaterializationError(
        expected="one complete schema-bound Analysis exchange stream",
        received=received,
        repair="Correct the selected producer or input binding and retry the action.",
        stage="exchange",
    )


class ValidatedExchangeStream:
    """One-shot private S0 consumer; completion follows exhaustion and owned close."""

    def __init__(
        self, source: BatchStream, binding: ExchangeBinding | ExchangeStreamBinding
    ) -> None:
        try:
            matches = source.schema.equals(binding.schema, check_metadata=False)
        except Exception:
            matches = False
        if not matches:
            with suppress(Exception):
                source.close()
            raise _exchange_error("stream schema differs")
        self._source = source
        self._binding = binding
        self._started = False
        self._closed = False
        self._close_failed = False
        self._active: Iterator[pa.RecordBatch] | None = None
        self.completed = False

    @property
    def schema(self) -> pa.Schema:
        return self._binding.schema

    def __iter__(self) -> Iterator[pa.RecordBatch]:
        if self._started or self._closed:
            raise _exchange_error("stream was already consumed or closed")
        self._started = True
        self._active = self._iterate()
        return self._active

    def _check_cells(self, batch: pa.RecordBatch) -> None:
        if isinstance(self._binding, ExchangeStreamBinding):
            if self._binding.kind == "relation":
                return
            allowed = dict(self._binding.cell_reasons)
        else:
            allowed = dict(self._binding.method.cell_reasons)
        values = batch.column("value")
        tags = batch.column("cell_tag")
        reasons = batch.column("cell_reason")
        for index in range(batch.num_rows):
            tag = tags[index].as_py()
            reason = reasons[index].as_py()
            defined = tag == "defined"
            if defined:
                if not values[index].is_valid or reason is not None:
                    raise _exchange_error("invalid Defined payload or reason")
            elif (
                tag not in ("null", "undefined", "unknown")
                or values[index].is_valid
                or reason not in allowed.get(tag, ())
            ):
                raise _exchange_error("invalid non-Defined payload, tag or reason")

    def _iterate(self) -> Iterator[pa.RecordBatch]:
        from marivo.analysis.materialization.storage import _RowValidator

        validator = _RowValidator(self._binding.row, self._binding.rows, source_key_validation=True)
        seen = False
        native_failed = False
        close_failed = False
        try:
            for batch in self._source:
                seen = True
                if not batch.schema.equals(self.schema, check_metadata=False):
                    raise _exchange_error("batch schema changed")
                validator.accept(batch)
                self._check_cells(batch)
                yield batch
            if not seen:
                yield pa.RecordBatch.from_arrays(
                    [pa.array([], type=field.type) for field in self.schema], schema=self.schema
                )
            validator.finish()
        except MaterializationError:
            raise
        except Exception:
            native_failed = True
        finally:
            if not self._closed:
                self._closed = True
                try:
                    self._source.close()
                except Exception:
                    close_failed = True
                    self._close_failed = True
        if native_failed or close_failed:
            raise _exchange_error("producer iteration or close failed")
        self.completed = True

    def close(self) -> None:
        failed = False
        if self._active is not None:
            close = getattr(self._active, "close", None)
            if close is not None:
                try:
                    close()
                except Exception:
                    failed = True
        if not self._closed:
            self._closed = True
            try:
                self._source.close()
            except Exception:
                failed = True
        if failed or self._close_failed:
            raise _exchange_error("producer close failed")
