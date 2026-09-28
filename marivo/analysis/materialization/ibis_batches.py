"""Owned Arrow batches returned by an Ibis backend."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import suppress

import pyarrow as pa

from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.submissions import Submission


class IbisBatchStream:
    """Keep the declared Arrow schema and close the Ibis reader on every exit path."""

    def __init__(
        self, native: pa.RecordBatchReader, schema: pa.Schema, receipt: Submission | None = None
    ) -> None:
        self._receipt = receipt
        self._native = native
        try:
            self._reader = pa.RecordBatchReader.from_batches(schema, native)
        except BaseException:
            with suppress(BaseException):
                native.close()
            raise

    @property
    def schema(self) -> pa.Schema:
        return self._reader.schema

    def __iter__(self) -> Iterator[pa.RecordBatch]:
        try:
            yield from self._reader
        except BaseException as error:
            if self._receipt is not None:
                self._receipt.fail(error)
            raise

    def close(self) -> None:
        try:
            self._reader.close()
        except BaseException:
            with suppress(BaseException):
                self._native.close()
            raise
        self._native.close()


class ProjectedBatchStream:
    """Validate an Ibis wide stream while exposing its exact primary fields."""

    def __init__(
        self, source: IbisBatchStream, full_schema: pa.Schema, primary_schema: pa.Schema
    ) -> None:
        self._source = source
        self._full_schema = full_schema
        self.schema = primary_schema
        self.full_batches: list[pa.RecordBatch] = []
        self._raw_types: tuple[pa.DataType, ...] | None = None

    def __iter__(self) -> Iterator[pa.RecordBatch]:
        for batch in self._source:
            physical = tuple(field.type for field in batch.schema)
            if batch.schema.names != self._full_schema.names or (
                self._raw_types is not None and physical != self._raw_types
            ):
                raise MaterializationError(
                    expected="stable Ibis batch fields and physical types",
                    received="source batch schema changed",
                    repair="Correct the source lowering or backend batch adaptation.",
                    stage="source_transfer",
                )
            for key in ("member", "group"):
                if key in batch.schema.names and batch.column(key).null_count:
                    raise MaterializationError(
                        expected="complete unique explicit keys",
                        received="missing or duplicate key",
                        repair="Correct the selected source key declaration or rows.",
                        stage="source_validation",
                    )
            try:
                normalized = batch.cast(self._full_schema, safe=True)
            except (pa.ArrowException, ValueError):
                raise MaterializationError(
                    expected="lossless checked Ibis output types",
                    received="source batch type or integer range differs",
                    repair="Use a method whose numeric type is admitted by the source adapter.",
                    stage="source_transfer",
                ) from None
            self._raw_types = physical
            self.full_batches.append(normalized)
            yield normalized.select(self.schema.names).cast(self.schema, safe=True)

    def close(self) -> None:
        self._source.close()
