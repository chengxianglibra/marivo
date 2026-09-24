"""Private W2 J1 receipt binding and complete local input decode."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

import pyarrow as pa

from marivo.analysis.datasets.descriptors import (
    DatasetRowContract,
    DatasetRowSetContract,
    _row_contract_fingerprint,
    _row_set_contract_fingerprint,
)
from marivo.analysis.materialization.contracts import (
    ExchangeBinding,
    ExchangePart,
    LocalReceipt,
    RetainedPart,
)
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.execution import ValidatedExchangeStream
from marivo.analysis.materialization.reads import (
    open_receipt_batch_stream,
    read_part_batches,
    read_table,
)
from marivo.analysis.materialization.storage import ReadPolicy, check_exchange_parts
from marivo.analysis.observation.dsl_j1 import J1_SUM_PARTS, J1Observed, J1Read, j1_row_contracts
from marivo.analysis.operators.dsl_j1_contracts import J1_OBSERVE_SUM
from marivo.analysis.operators.dsl_j1_values import J1ExecutionResult

_STATE_COLUMNS = ("state_sum", "non_null_count", "row_count")
_PART_CONTRACTS = (
    "dsl.j1.value_sum",
    "dsl.j1.non_null_count",
    "dsl.j1.row_count",
)
_DEFAULT_READ_POLICY = ReadPolicy()


@dataclass(frozen=True, slots=True)
class J1ReadReceipt:
    """Exact private read definition and its selected local receipt."""

    read: J1Read
    row: DatasetRowContract
    rows: DatasetRowSetContract
    receipt: LocalReceipt


def j1_read_receipt(read: J1Read, receipt: LocalReceipt) -> J1ReadReceipt:
    """Bind one complete retained category read to its original definition."""
    row, rows = j1_row_contracts(read.members_input.context, read.root)
    if (
        _row_contract_fingerprint(row) != read.root.row_contract_fingerprint
        or _row_set_contract_fingerprint(rows) != read.root.row_set_contract_fingerprint
    ):
        raise MaterializationError(
            expected="exact J1 read row contract",
            received="definition binding differs",
            repair="Rebuild the category read from its selected Semantic declaration.",
            stage="exchange",
        )
    return J1ReadReceipt(read, row, rows, receipt)


def load_j1_read(
    project_root: Path, binding: J1ReadReceipt, *, policy: ReadPolicy = _DEFAULT_READ_POLICY
) -> J1ExecutionResult:
    """Validate the full fixed read receipt before enabling pandas selection."""
    expected_row, expected_rows = j1_row_contracts(
        binding.read.members_input.context, binding.read.root
    )
    if (
        _row_contract_fingerprint(expected_row) != _row_contract_fingerprint(binding.row)
        or _row_set_contract_fingerprint(expected_rows)
        != _row_set_contract_fingerprint(binding.rows)
        or _row_contract_fingerprint(binding.row) != binding.read.root.row_contract_fingerprint
    ):
        raise MaterializationError(
            expected="exact retained J1 read definition",
            received="foreign read contract",
            repair="Select the receipt bound to this exact category read.",
            stage="local_admission",
        )
    table = read_table(
        project_root=project_root,
        receipt=binding.receipt,
        row_contract=binding.row,
        row_set_contract=binding.rows,
        policy=policy,
    )
    return J1ExecutionResult(binding.read.root, table)


def j1_observation_binding(
    observed: J1Observed,
    result: J1ExecutionResult,
    *,
    input_binding: str,
    receipt: LocalReceipt | None = None,
    retained_parts: tuple[RetainedPart, ...] = (),
) -> ExchangeBinding:
    """Bind a checked J1 primary and state to the existing exchange contract."""
    if result.root is not observed.root or observed.coordinates:
        raise MaterializationError(
            expected="one exact coordinate-free J1 observation",
            received="foreign root or unqualified coordinate state",
            repair="Bind the selected complete J1 observation and its original state.",
            stage="exchange",
        )
    row, rows = j1_row_contracts(observed.context, observed.root)
    if (
        _row_contract_fingerprint(row) != observed.root.row_contract_fingerprint
        or _row_set_contract_fingerprint(rows) != observed.root.row_set_contract_fingerprint
        or observed.plan.required_parts != J1_SUM_PARTS
    ):
        raise MaterializationError(
            expected="exact J1 row and state definition",
            received="definition binding differs",
            repair="Rebuild the observation from the current Semantic declarations.",
            stage="exchange",
        )
    names = [field.name for field in row.schema.columns]
    wide = result.primary
    if not {*names, *_STATE_COLUMNS} <= set(wide.column_names):
        raise MaterializationError(
            expected="complete primary and original sum/count state",
            received="missing J1 columns",
            repair="Execute the admitted J1 observation with all retained parts.",
            stage="exchange",
        )
    keys = tuple(field.name for field in row.schema.columns if field.field_id in row.key_field_ids)
    schema = pa.schema(
        [
            pa.field(name, wide.schema.field(name).type, nullable=(name not in (*keys, "cell_tag")))
            for name in names
        ]
    )
    parts = tuple(
        ExchangePart(
            role,
            contract_id,
            1,
            pa.schema(
                [
                    *(schema.field(key) for key in keys),
                    pa.field(
                        column, wide.schema.field(column).type, nullable=(column == "state_sum")
                    ),
                ]
            ),
            keys,
        )
        for role, contract_id, column in zip(
            J1_SUM_PARTS, _PART_CONTRACTS, _STATE_COLUMNS, strict=True
        )
    )
    evidence = observed.plan.evidence
    completed = tuple(dict.fromkeys((*evidence.completed_checks, *result.completed_checks)))
    evidence = replace(
        evidence,
        completed_checks=completed,
        pending_checks=tuple(check for check in evidence.pending_checks if check not in completed),
    )
    binding = ExchangeBinding(
        row=row,
        rows=rows,
        domain=observed.domain,
        quantity=observed.quantity,
        method=J1_OBSERVE_SUM.contract,
        evidence=evidence,
        input_binding=input_binding,
        schema=schema,
        parts=parts,
        storage_receipt=receipt,
        retained_parts=retained_parts,
    )
    binding.require_method_type()
    return binding


def load_j1_observation(
    project_root: Path,
    observed: J1Observed,
    binding: ExchangeBinding,
    *,
    policy: ReadPolicy = _DEFAULT_READ_POLICY,
) -> J1ExecutionResult:
    """Exhaust exact receipt and keyed parts before enabling pandas continuation."""
    receipt = binding.storage_receipt
    if (
        receipt is None
        or binding.method.method_id != J1_OBSERVE_SUM.contract.method_id
        or binding.row.shape_id != observed.root.shape_id
        or _row_contract_fingerprint(binding.row) != observed.root.row_contract_fingerprint
    ):
        raise MaterializationError(
            expected="one exact J1 observation receipt and method",
            received="foreign retained input",
            repair="Select the receipt and method bound to this observation definition.",
            stage="local_admission",
        )
    binding.require_method_type()
    stream = ValidatedExchangeStream(
        open_receipt_batch_stream(project_root, receipt, binding.row, binding.rows, policy=policy),
        binding,
    )
    try:
        primary = pa.Table.from_batches(tuple(stream), schema=binding.schema)
    finally:
        stream.close()
    if not stream.completed:
        raise MaterializationError(
            expected="complete receipt input validation",
            received="input was not exhausted",
            repair="Read the complete selected receipt before continuing.",
            stage="local_admission",
        )
    parts: dict[str, pa.Table] = {}
    for specification, retained in zip(binding.parts, binding.retained_parts, strict=True):
        reader = read_part_batches(
            project_root, retained, expected_schema=specification.schema, policy=policy
        )
        try:
            parts[specification.role] = pa.Table.from_batches(
                tuple(reader), schema=specification.schema
            )
        finally:
            reader.close()
    aligned = check_exchange_parts(primary, parts, binding)
    wide = primary
    for role, column in zip(J1_SUM_PARTS, _STATE_COLUMNS, strict=True):
        wide = wide.append_column(column, aligned[role][column])
    return J1ExecutionResult(
        observed.root, wide, completed_checks=binding.evidence.completed_checks
    )
