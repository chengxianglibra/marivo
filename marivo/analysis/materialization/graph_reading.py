"""Committed terminal output reads; no computation or persistent data cache."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from marivo.analysis.core.artifact_data import (
    ArtifactManifest,
    DataSchema,
    OutputEvidence,
    OutputFinding,
    ParquetHandle,
)
from marivo.analysis.dsl.artifact import Artifact
from marivo.analysis.errors import ArtifactNotFoundError
from marivo.analysis.evidence._dataset_types import ArtifactDigest, Finding, FindingPage
from marivo.analysis.materialization import terminal_store
from marivo.analysis.materialization.contracts import digest, parse_timestamp
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.graph_exchange import normalize_stage_frame
from marivo.analysis.materialization.storage import (
    _checked_path,
    _manifest_bytes,
    _open_payload,
    payload_digest,
)
from marivo.analysis.materialization.store import SessionStore, _one, _text
from marivo.analysis.materialization.terminal_protocol import invalid
from marivo.analysis.materialization.terminal_storage import (
    POLICY,
    arrow_schema,
    physical_schema,
)
from marivo.analysis.refs import ArtifactRef
from marivo.render import Card


@dataclass(frozen=True, slots=True)
class PreviewRead:
    n: int


@dataclass(frozen=True, slots=True)
class ExportRead:
    pass


@dataclass(frozen=True, slots=True)
class ReadLayout:
    schema: DataSchema
    handle: ParquetHandle
    positions: tuple[int, ...] | None = None


class OutputRead:
    """One call's receipt validation and Arrow lifetime, shared by its consumed parts."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._validated: set[ParquetHandle] = set()

    def _open(self, layout: ReadLayout) -> pq.ParquetFile:
        handle = layout.handle
        receipt = handle.receipt
        directory = _checked_path(self.root, Path(receipt.project_relative_path))
        if handle not in self._validated:
            if payload_digest(self.root, directory, receipt) != handle.payload_digest:
                raise invalid("terminal payload digest differs from its committed receipt")
            if (directory / "manifest.json").read_bytes() != _manifest_bytes(receipt.file_manifest):
                raise invalid("terminal file manifest differs from its receipt")
            self._validated.add(handle)
        parquet, path = _open_payload(self.root, receipt)
        if (
            not parquet.schema_arrow.equals(physical_schema(layout.schema), check_metadata=True)
            or parquet.metadata.num_rows != receipt.realized_row_count
            or path.stat().st_size != receipt.file_manifest[0].size_bytes
            or receipt.realized_byte_count
            != path.stat().st_size + len(_manifest_bytes(receipt.file_manifest))
        ):
            parquet.close()
            raise invalid("terminal payload schema, rows or bytes differ from its receipt")
        return parquet

    def read(self, layout: ReadLayout, request: PreviewRead | ExportRead) -> pd.DataFrame:
        schema = layout.schema
        count = layout.handle.receipt.realized_row_count
        if layout.positions is not None:
            positions = layout.positions
            if any(type(index) is not int or not 0 <= index < count for index in positions) or len(
                set(positions)
            ) != len(positions):
                raise invalid(
                    "display order contains duplicate or out-of-range primary row positions"
                )
            if not positions:
                if isinstance(request, ExportRead):
                    with self._open(layout):
                        pass
                return normalize_stage_frame(
                    pd.DataFrame(columns=[column.slot_id for column in schema.columns]),
                    schema.columns,
                    schema.key_fields,
                )
            chunks: list[pd.DataFrame] = []
            with self._open(layout) as parquet:
                offset = 0
                requested = set(positions)
                for group in range(parquet.num_row_groups):
                    length = parquet.metadata.row_group(group).num_rows
                    selected = sorted(
                        index for index in requested if offset <= index < offset + length
                    )
                    if selected:
                        table = parquet.read_row_group(group).cast(arrow_schema(schema), safe=True)
                        chunk = table.take(
                            pa.array([index - offset for index in selected], type=pa.int64())
                        ).to_pandas(types_mapper=pd.ArrowDtype)
                        chunk.index = pd.Index(selected)
                        chunks.append(chunk)
                    offset += length
            frame = pd.concat(chunks).loc[list(positions)].reset_index(drop=True)
            return normalize_stage_frame(frame, schema.columns, schema.key_fields)
        remaining = min(request.n, count) if isinstance(request, PreviewRead) else count
        batches: list[pa.RecordBatch] = []
        # A zero-row preview is metadata-only, including zero-column outputs.
        if remaining:
            with self._open(layout) as parquet:
                for group in range(parquet.num_row_groups):
                    if not remaining:
                        break
                    size = min(POLICY.batch_rows, remaining)
                    for batch in parquet.iter_batches(batch_size=size, row_groups=[group]):
                        batches.append(batch)
                        remaining -= batch.num_rows
                        if not remaining:
                            break
                        # The active Arrow reader applies this size to its next batch.
                        parquet.reader.set_batch_size(min(POLICY.batch_rows, remaining))
        elif isinstance(request, ExportRead):
            # Full exports also verify an empty payload's receipt and frozen schema.
            with self._open(layout):
                pass
        table = pa.Table.from_batches(batches, schema=physical_schema(schema)).cast(
            arrow_schema(schema), safe=True
        )
        if not schema.columns:
            if count > 1 or any(value != 1 for value in table["__unit"].to_pylist()):
                raise invalid("invalid zero-axis unit output")
            return pd.DataFrame(index=pd.RangeIndex(table.num_rows))
        frame = table.to_pandas(types_mapper=pd.ArrowDtype)
        normalized = normalize_stage_frame(frame, schema.columns, schema.key_fields)
        for column in schema.columns:
            array = table[column.slot_id]
            if pa.types.is_floating(array.type) and pc.any(pc.invert(pc.is_finite(array))).as_py():
                raise invalid("decoded terminal floating output contains non-finite values")
        tied = pd.Series(True, index=pd.RangeIndex(max(0, len(frame) - 1)))
        for key in schema.key_fields:
            left = frame[key].iloc[:-1].reset_index(drop=True)
            right = frame[key].iloc[1:].reset_index(drop=True)
            if (tied & ((left.notna() & right.isna()) | (left > right).fillna(False))).any():
                raise invalid("decoded terminal rows are not in canonical complete-key order")
            tied &= (left == right).fillna(False) | (left.isna() & right.isna())
        return normalized


@dataclass(frozen=True, slots=True)
class TerminalReader:
    """Store authority only; every data call reads committed Parquet anew."""

    store: SessionStore

    def _manifest(self, value: ArtifactManifest) -> ArtifactManifest:
        with self.store._read() as conn:
            saved = terminal_store.manifest(self.store, conn, value.artifact_id)
            if saved != value:
                raise invalid("selected terminal Artifact metadata changed or disappeared")
            terminal_store.collection(conn, value.artifact_id)
        return value

    def _frame(self, manifest: ArtifactManifest, request: PreviewRead | ExportRead) -> pd.DataFrame:
        from marivo.analysis.materialization.pandas_export import export_frame

        self._manifest(manifest)
        try:
            return export_frame(manifest, OutputRead(self.store.project_root), request)
        except (
            pa.ArrowException,
            OSError,
            ValueError,
            TypeError,
            OverflowError,
            MemoryError,
        ) as error:
            raise MaterializationError(
                expected="a complete lossless read of the selected committed output",
                received=f"{type(error).__name__}: {error}",
                repair="Preserve the output files. Check the selected Artifact receipts and available memory; export cannot return partial rows.",
                stage="terminal_reading",
                help_target="Artifact",
            ) from error

    def show(self, manifest: ArtifactManifest, *, n: int) -> None:
        frame = self._frame(manifest, PreviewRead(n))
        facts, _ = terminal_store.records_page(
            self.store, manifest.artifact_id, kind="evidence", limit=3
        )
        card = Card(
            identity=f"Artifact id={manifest.artifact_id} session={manifest.session_id}",
            available=(".to_pandas()", ".evidence_digest()"),
            omission_recovery="use .to_pandas() for complete data and .evidence_digest() for all saved facts",
        ).field("rows", str(manifest.data.rows.receipt.realized_row_count))
        card.table(
            tuple(str(column) for column in frame.columns),
            tuple(
                tuple(str(value)[:256] for value in row)
                for row in frame.itertuples(index=False, name=None)
            ),
            label="output",
        )
        for index, item in enumerate(facts):
            if not isinstance(item, OutputEvidence):
                raise invalid("terminal Evidence contains another record kind")
            card.field(f"evidence.{index}", f"{item.kind}: {item.summary}"[:512])
        print(card.render())

    def to_pandas(self, manifest: ArtifactManifest) -> pd.DataFrame:
        return self._frame(manifest, ExportRead())

    def findings(
        self, manifest: ArtifactManifest, *, limit: int, cursor: str | None
    ) -> FindingPage:
        self._manifest(manifest)
        items, next_cursor = terminal_store.records_page(
            self.store, manifest.artifact_id, kind="finding", limit=limit, cursor=cursor
        )
        with self.store._read() as conn:
            row = _one(
                conn,
                "SELECT committed_at FROM dataset_artifacts WHERE artifact_ref=?",
                (manifest.artifact_id,),
            )
            if row is None:
                raise invalid("selected terminal Artifact disappeared")
            committed_at = parse_timestamp(_text(row, "committed_at"))
        findings: list[Finding] = []
        for item in items:
            if not isinstance(item, OutputFinding):
                raise invalid("terminal Finding page contains another record kind")
            findings.append(
                Finding(
                    finding_id=item.finding_id,
                    artifact_ref=ArtifactRef(ref=manifest.artifact_id),
                    session_id=manifest.session_id,
                    value=item.value,
                    coordinates=item.coordinates,
                    committed_at=committed_at,
                )
            )
        return FindingPage(tuple(findings), limit, next_cursor is not None, next_cursor)

    def evidence_digest(self, manifest: ArtifactManifest) -> ArtifactDigest:
        self._manifest(manifest)
        saved, items = terminal_store.evidence(self.store, manifest.artifact_id)
        return ArtifactDigest(
            artifact_ref=ArtifactRef(ref=manifest.artifact_id),
            items=tuple(items),
            quality_summary_digest=saved.evidence_digest,
            typed_issue_digest=digest(()),
            evidence_digest=saved.evidence_digest,
            finding_count=saved.finding_count,
            finding_set_digest=saved.finding_set_digest,
        )


def open_artifact(store: SessionStore, session_id: str, artifact_id: str) -> Artifact:
    with store._read() as conn:
        # Ownership is checked before exposing metadata from another Session.
        row = _one(
            conn, "SELECT session_ref FROM dataset_artifacts WHERE artifact_ref=?", (artifact_id,)
        )
        if row is None or _text(row, "session_ref") != session_id:
            raise ArtifactNotFoundError.for_ref(artifact_id)
        manifest = terminal_store.manifest(store, conn, artifact_id)
        if manifest is None:
            raise ArtifactNotFoundError.for_ref(artifact_id)
        terminal_store.collection(conn, artifact_id)
    return Artifact._from_manifest(manifest, TerminalReader(store))
