"""Extract decomposition_item findings from an AttributionFrame DataFrame."""

from __future__ import annotations

# mypy: disable-error-code=import-untyped
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, cast

import pandas as pd

from marivo.analysis.evidence.extraction._coordinates import normalize_coordinate_value
from marivo.analysis.evidence.identity import make_finding_id, make_typed_item_key
from marivo.analysis.evidence.types import (
    ContributionFindingValue,
    DerivationRule,
    Direction,
    Finding,
    Subject,
)
from marivo.analysis.frames._attribution_columns import ATTRIBUTION_LEVEL_COLUMN
from marivo.refs import RefPayloadV1

_RESERVED_COLUMNS = {
    "dimension",
    "contribution_value",
    "contribution_share",
    "contribution",
    "share_of_total_delta",
    "share_of_positive_pool",
    "share_of_negative_pool",
    "rank",
    "direction",
    "method",
    "reconciliation_residual",
}


@dataclass(frozen=True)
class DecompositionExtractionContract:
    """Map an AttributionFrame contract without borrowing user column names."""

    dimension_name: str
    key_columns: tuple[str, ...]
    contribution_column: str
    contribution_share_column: str | None
    direction: Direction
    decomposition_method: str
    reconciliation_residual: float | None
    ordered_axis_refs: tuple[RefPayloadV1, ...] = ()
    ordered_prefix_rows: bool = False
    rollup_safe: bool | None = None
    causal_claim: Literal["none"] = "none"
    source_error_bound: float | None = None


def _is_missing(value: Any) -> bool:
    return bool(pd.isna(value)) if not isinstance(value, (list, tuple, dict)) else False


def _key_tuple(dimension: str, keys: dict[str, Any]) -> str:
    return make_typed_item_key(
        namespace="decomposition_item",
        context={"dimension": dimension},
        coordinates=keys,
    )


def _to_float(value: Any) -> float | None:
    if value is None or _is_missing(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def extract_decomposition_findings(
    *,
    df: pd.DataFrame,
    artifact_id: str,
    session_id: str,
    subject: Subject,
    committed_at: datetime,
    scope_delta_ref: str,
    contract: DecompositionExtractionContract | None = None,
) -> list[Finding]:
    """Extract one finding per contribution row."""
    if df.empty:
        return []

    findings: list[Finding] = []
    ranked_rows = list(df.iterrows())
    contribution_column = (
        contract.contribution_column if contract is not None else "contribution_value"
    )
    ranked_rows.sort(key=lambda entry: -abs(_to_float(entry[1].get(contribution_column)) or 0.0))
    for rank, (_, row) in enumerate(ranked_rows, start=1):
        persisted_rank = _to_float(row.get("rank")) if contract is not None else None
        contribution_rank = (
            int(persisted_rank)
            if persisted_rank is not None and persisted_rank >= 1 and persisted_rank.is_integer()
            else rank
        )
        if contract is None:
            dimension = str(row.get("dimension", ""))
            identity_keys = {
                column: row[column] for column in df.columns if column not in _RESERVED_COLUMNS
            }
            keys = {
                column: normalize_coordinate_value(row[column])
                for column in df.columns
                if column not in _RESERVED_COLUMNS
            }
            contribution_value = _to_float(row.get("contribution_value"))
            contribution_share = _to_float(row.get("contribution_share"))
            direction = cast("Direction", row.get("direction") or "undefined")
            decomposition_method = str(row.get("method") or "algebraic_decomposition")
            reconciliation_residual = _to_float(row.get("reconciliation_residual"))
        else:
            resolution_refs = contract.ordered_axis_refs
            if contract.ordered_prefix_rows:
                level_value = row.get(ATTRIBUTION_LEVEL_COLUMN)
                level = int(level_value) if level_value is not None else 0
                resolution_refs = resolution_refs[:level]
            identity_dimension = (
                " > ".join(ref.path for ref in resolution_refs)
                if resolution_refs
                else contract.dimension_name
            )
            dimension = contract.dimension_name
            identity_keys = {
                column: row[column] for column in contract.key_columns if column in df.columns
            }
            keys = {
                column: normalize_coordinate_value(row[column])
                for column in contract.key_columns
                if column in df.columns
            }
            contribution_value = _to_float(row.get(contract.contribution_column))
            contribution_share = (
                _to_float(row.get(contract.contribution_share_column))
                if contract.contribution_share_column is not None
                else None
            )
            direction = contract.direction
            decomposition_method = contract.decomposition_method
            reconciliation_residual = contract.reconciliation_residual
        item_key = _key_tuple(
            identity_dimension if contract is not None else dimension,
            identity_keys,
        )
        findings.append(
            Finding(
                finding_id=make_finding_id(
                    artifact_id=artifact_id,
                    finding_type="decomposition_item",
                    canonical_item_key=item_key,
                ),
                finding_type="decomposition_item",
                epistemic_kind="algebraic",
                artifact_id=artifact_id,
                session_id=session_id,
                subject=subject,
                canonical_item_key=item_key,
                value=ContributionFindingValue(
                    dimension=dimension,
                    dimension_keys=keys,
                    contribution_value=contribution_value,
                    contribution_share=contribution_share,
                    contribution_rank=contribution_rank,
                    direction=direction,
                    decomposition_method=decomposition_method,
                    reconciliation_residual=reconciliation_residual,
                    scope_delta_ref=scope_delta_ref,
                    resolution_axis_refs=resolution_refs if contract is not None else (),
                    rollup_safe=contract.rollup_safe if contract is not None else None,
                    causal_claim=contract.causal_claim if contract is not None else "none",
                    contribution_std_error=(
                        _to_float(row.get("contribution_std_error"))
                        if contract is not None
                        else None
                    ),
                    source_error_bound=(
                        contract.source_error_bound if contract is not None else None
                    ),
                ),
                derivation=DerivationRule(
                    rule_id="extract.contribution",
                    rule_version="v3",
                    operator="attribute",
                    source_fields=tuple(str(column) for column in df.columns),
                    source_finding_refs=(),
                ),
                source_refs=(scope_delta_ref,),
                committed_at=committed_at,
            )
        )
    return findings


__all__ = ["DecompositionExtractionContract", "extract_decomposition_findings"]
