"""Private first-route execution from pure definition to committed Dataset authority."""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING
from uuid import uuid4

import pyarrow as pa

from marivo.analysis.datasets.base import LogicalDataset
from marivo.analysis.materialization.contracts import (
    StorageReceipt,
)
from marivo.analysis.materialization.resources import (
    reserve_output,
)
from marivo.analysis.materialization.storage import (
    DatasetWriteResult,
    IndependentPartWrite,
    PartWriteSpec,
    StoragePolicy,
    write_local_dataset,
)

if TYPE_CHECKING:
    from marivo.analysis.materialization.admission import DatasetRuntime

_LOCAL_STORAGE_POLICY = StoragePolicy()


def write_output(
    self: DatasetRuntime,
    dataset: LogicalDataset,
    batches: Iterable[pa.RecordBatch],
    run_ref: str,
    *,
    parts: tuple[PartWriteSpec, ...] = (),
    independent_parts: tuple[IndependentPartWrite, ...] = (),
    source_key_validation: bool,
) -> tuple[str, DatasetWriteResult[StorageReceipt]]:
    nonce = uuid4().hex
    artifact_ref = "artifact_" + nonce
    staging, final, _ = reserve_output(
        self.store,
        run_ref=run_ref,
        session_ref=self.session_ref,
        artifact_ref=artifact_ref,
        nonce=nonce,
    )
    self._event("output_reserved")
    storage = write_local_dataset(
        project_root=self.store.project_root,
        staging_path=staging,
        final_path=final,
        batches=batches,
        row_contract=dataset.row_contract,
        row_set_contract=dataset.row_set_contract,
        parts=parts,
        independent_parts=independent_parts,
        source_key_validation=source_key_validation,
        policy=_LOCAL_STORAGE_POLICY,
        event=self._event,
    )
    return artifact_ref, storage
