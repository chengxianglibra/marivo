"""Owned Arrow batches returned by an Ibis backend."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import suppress

import pyarrow as pa

from marivo.analysis.materialization.submissions import Submission


class IbisBatchStream:
    """Keep the declared Arrow schema and close the Ibis reader on every exit path."""

    def __init__(
        self, native: pa.RecordBatchReader, schema: pa.Schema, receipt: Submission | None = None
    ) -> None:
        self._receipt = receipt
        self._native = native
        self._reader = pa.RecordBatchReader.from_batches(schema, native)

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
