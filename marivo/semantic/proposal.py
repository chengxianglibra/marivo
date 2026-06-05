# marivo/semantic/proposal.py
"""Structural candidate proposal from datasource metadata.

Pure and deterministic: takes a TableMetadata, returns Candidates. Structure only,
never business meaning (contracts spec, Mechanism 1). The agent adds meaning
downstream; the classifier consumes these candidates.

Import boundary: this module uses Protocols for metadata shapes so that
marivo.semantic does not depend on marivo.analysis. The caller passes concrete
TableMetadata objects which satisfy these Protocols duck-typingly.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from marivo.semantic.classifier import Candidate, EvidenceRef
from marivo.semantic.ir import DatasetSourceIR, source_name, source_to_dict

_TEMPORAL_TYPE = re.compile(r"date|time|timestamp", re.IGNORECASE)
_TEMPORAL_NAME = re.compile(r"(^|_)(dt|date|time|ts|at)($|_)", re.IGNORECASE)
_ENUM_NAME = re.compile(r"(^|_)(status|state|type|code|flag|kind|category)($|_)", re.IGNORECASE)


@runtime_checkable
class _ColumnMeta(Protocol):
    @property
    def name(self) -> str: ...
    @property
    def type(self) -> str: ...
    @property
    def nullable(self) -> bool | None: ...
    @property
    def comment(self) -> str | None: ...


@runtime_checkable
class _TableMeta(Protocol):
    @property
    def datasource(self) -> str: ...
    @property
    def table(self) -> str: ...
    @property
    def database(self) -> str | tuple[str, ...] | None: ...
    @property
    def comment(self) -> str | None: ...
    @property
    def columns(self) -> Sequence[_ColumnMeta]: ...
    @property
    def is_view(self) -> bool: ...
    @property
    def view_definition(self) -> str | None: ...


@dataclass(frozen=True)
class ResidualColumn:
    dataset: str  # owning proposed dataset id, e.g. "sales.orders"
    column: str  # column name
    data_type: str  # raw type string, passed through verbatim
    nullable: bool | None
    comment: str | None  # column comment if present


@dataclass(frozen=True)
class ProposalResult:
    candidates: tuple[Candidate, ...]  # same order and content as today
    residual_columns: tuple[ResidualColumn, ...]  # columns no heuristic matched


def _qualify(model: str, name: str) -> str:
    return f"{model}.{name}"


def _comment_evidence(locator: str, comment: str | None) -> tuple[EvidenceRef, ...]:
    if comment:
        return (EvidenceRef(evidence_type="comment", locator=locator, excerpt=comment),)
    return ()


def _view_definition_evidence(ref: str, metadata: _TableMeta) -> tuple[EvidenceRef, ...]:
    if getattr(metadata, "is_view", False) and metadata.view_definition:
        return (
            EvidenceRef(
                evidence_type="view_definition",
                locator=f"view_definition:{ref}",
                excerpt=metadata.view_definition,
            ),
        )
    return ()


def _database_label(database: str | tuple[str, ...] | None) -> str | None:
    if database is None:
        return None
    return ".".join(database) if isinstance(database, tuple) else database


def _metadata_ref(datasource: str, table: str, database: str | tuple[str, ...] | None) -> str:
    database_label = _database_label(database)
    if database_label is None:
        return f"{datasource}.{table}"
    return f"{datasource}.{database_label}.{table}"


def _source_slot(table: str, database: str | tuple[str, ...] | None) -> dict[str, object]:
    database_value: str | list[str] | None = (
        list(database) if isinstance(database, tuple) else database
    )
    return {"kind": "table", "table": table, "database": database_value}


def candidates_from_metadata(
    metadata: _TableMeta,
    *,
    model: str,
    source: DatasetSourceIR | None = None,
) -> tuple[Candidate, ...]:
    table = source_name(source) if source is not None else metadata.table
    datasource = metadata.datasource
    ref = _metadata_ref(datasource, table, metadata.database)
    source_slot = (
        source_to_dict(source) if source is not None else _source_slot(table, metadata.database)
    )
    out: list[Candidate] = [
        Candidate(
            object_kind="dataset",
            proposed_id=_qualify(model, table),
            decision_kind="dataset_identity",
            slot_values={
                "datasource": datasource,
                "table": table,
                "database": _database_label(metadata.database),
                "source": source_slot,
            },
            evidence=(
                EvidenceRef("metadata", f"metadata:{ref}"),
                *_comment_evidence(f"comment:{table}", metadata.comment),
                *_view_definition_evidence(ref, metadata),
            ),
            semantic_delta=f"declare dataset {model}.{table} over {ref}",
        )
    ]
    for col in metadata.columns:
        locator = f"metadata:{ref}.{col.name}"
        col_comment = _comment_evidence(f"comment:{table}.{col.name}", col.comment)
        if _TEMPORAL_TYPE.search(col.type) or _TEMPORAL_NAME.search(col.name):
            out.append(
                Candidate(
                    object_kind="time_field",
                    proposed_id=f"{model}.{table}.{col.name}",
                    decision_kind="time_field_identity",
                    slot_values={
                        "dataset": _qualify(model, table),
                        "column": col.name,
                        "data_type_hint": col.type,
                    },
                    evidence=(EvidenceRef("metadata", locator), *col_comment),
                    semantic_delta=f"candidate business time axis: {col.name}",
                )
            )
        if _ENUM_NAME.search(col.name):
            out.append(
                Candidate(
                    object_kind="field",
                    proposed_id=f"{model}.{table}.{col.name}",
                    decision_kind="field_meaning",
                    slot_values={"dataset": _qualify(model, table), "column": col.name},
                    evidence=(EvidenceRef("metadata", locator), *col_comment),
                    semantic_delta=f"candidate enum/status field: {col.name}",
                )
            )
    return tuple(out)


def residual_columns(
    metadata: _TableMeta,
    candidates: Sequence[Candidate],
    *,
    model: str,
    source: DatasetSourceIR | None = None,
) -> tuple[ResidualColumn, ...]:
    """Columns of *metadata* not cited by any time_field or field candidate.

    A column is **covered** iff it appears in ``slot_values["column"]`` of a
    candidate whose ``object_kind`` is ``"time_field"`` or ``"field"``.  Dataset
    and relationship candidates do not cover specific columns.  Columns used as
    relationship join keys remain residual — they may still warrant a dimension
    or field declaration, so the agent should see them.

    Residual columns preserve source column order for deterministic output.
    """
    covered = {
        c.slot_values["column"] for c in candidates if c.object_kind in ("time_field", "field")
    }
    table = source_name(source) if source is not None else metadata.table
    dataset_id = _qualify(model, table)
    out: list[ResidualColumn] = []
    for col in metadata.columns:
        if col.name not in covered:
            out.append(
                ResidualColumn(
                    dataset=dataset_id,
                    column=col.name,
                    data_type=col.type,
                    nullable=col.nullable,
                    comment=col.comment,
                )
            )
    return tuple(out)


def _singular(table: str) -> str:
    """Naive English singularization: strips trailing 's' only. Intentionally
    simple; irregular plurals (addresses, indices) are not handled."""
    return table[:-1] if table.endswith("s") else table


def relationship_candidates(
    metadatas: Sequence[_TableMeta], *, model: str
) -> tuple[Candidate, ...]:
    """Propose a join when one table has a ``<other_singular>_id`` column and the
    other table has an ``id`` column. Structural signal only (key-name match).

    Bidirectional: each direction (A→B, B→A) is a separate candidate when both
    sides have matching FK columns."""
    columns_by_table = {m.table: {c.name for c in m.columns} for m in metadatas}
    out: list[Candidate] = []
    for src in metadatas:
        for dst in metadatas:
            if dst.table == src.table:
                continue
            fk = f"{_singular(dst.table)}_id"
            if fk in columns_by_table[src.table] and "id" in columns_by_table[dst.table]:
                out.append(
                    Candidate(
                        object_kind="relationship",
                        proposed_id=f"{model}.{src.table}_to_{dst.table}",
                        decision_kind="relationship_join_keys",
                        slot_values={
                            "from_dataset": f"{model}.{src.table}",
                            "to_dataset": f"{model}.{dst.table}",
                            "from_column": fk,
                            "to_column": "id",
                        },
                        evidence=(
                            EvidenceRef(
                                "structural",
                                f"keymatch:{src.table}.{fk}->{dst.table}.id",
                            ),
                        ),
                        semantic_delta=f"candidate join {src.table}.{fk} -> {dst.table}.id",
                    )
                )
    return tuple(out)


def detect_structural_conflict(slot_values: Mapping[str, object], metadata: _TableMeta) -> bool:
    """True if a same-table column referenced by the decision is absent from the
    schema. Only ``column`` and ``from_column`` are checked (they belong to ``metadata``'s
    table); cross-table refs such as ``to_column`` are validated elsewhere. Deeper
    conflict detection (SQL-parse, sample refutation) is a later phase."""
    column_names = {c.name for c in metadata.columns}
    for key in ("column", "from_column"):
        value = slot_values.get(key)
        if isinstance(value, str) and value not in column_names:
            return True
    return False
