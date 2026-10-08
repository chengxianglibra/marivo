"""Schema-first stream protocol shared by current graph exchanges."""

from collections.abc import Iterator
from typing import Protocol

import pyarrow as pa


class BatchStream(Protocol):
    @property
    def schema(self) -> pa.Schema: ...
    def __iter__(self) -> Iterator[pa.RecordBatch]: ...
    def close(self) -> None: ...
