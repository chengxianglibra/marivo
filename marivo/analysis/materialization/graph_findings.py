"""Frozen graph Finding extractors, policy authority and common collection reads."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Literal

import pandas as pd
import pyarrow as pa
from pydantic import TypeAdapter, ValidationError

from marivo.analysis._pages import decode_keyset_cursor, encode_keyset_cursor
from marivo.analysis.core.model import FindingPolicyPart
from marivo.analysis.evidence import _dataset_types as t
from marivo.analysis.evidence._dataset_codec import (
    decode_finding_body,
)
from marivo.analysis.materialization.contracts import parse_timestamp
from marivo.analysis.materialization.graph_exchange import (
    ArrowPart,
    ExchangePart,
    ExchangeResult,
)
from marivo.analysis.materialization.graph_protocol import (
    DESCRIPTOR,
    Descriptor,
    ValidatedDescriptor,
    digest,
    encode,
    invalid,
)
from marivo.analysis.refs import ArtifactRef

if TYPE_CHECKING:
    from marivo.analysis.materialization.store import SessionStore

CAP = 1000


@dataclass(frozen=True, slots=True)
class Policy:
    producer: Literal[
        "deviation.zscore",
        "deviation.mad",
        "time.runs",
        "association.pearson",
        "association.spearman",
        "association.kendall",
        "forecast.naive",
        "forecast.drift",
        "forecast.seasonal_naive",
    ]
    state_version: Literal["v1"]
    extractor: Literal[
        "graph.no_findings@v1",
        "graph.association_findings@v1",
        "graph.forecast_findings@v1",
    ]
    policy: Literal[
        "zero_findings@v1",
        "bounded_descriptive_findings@v1",
        "bounded_prediction_findings@v1",
    ]
    ordered_input_bindings: tuple[str, ...]
    eligible: int
    emitted: int
    truncated: int
    version: Literal["v1"] = "v1"


POLICY = TypeAdapter(Policy)


def _policy(parts: tuple[ExchangePart | ArrowPart, ...]) -> Policy:
    table = next(p.table for p in parts if p.role == "finding_policy")
    if isinstance(table, pd.DataFrame):
        payload = table["finding_policy__retained"].iloc[0]
    elif isinstance(table, pa.Table):
        payload = table["finding_policy__retained"][0].as_py()
    else:
        raise invalid("Finding policy is not a tabular retained part")
    if not isinstance(payload, str):
        raise invalid("Finding policy payload is not a JSON string")
    try:
        policy = POLICY.validate_json(payload, strict=True)
    except ValidationError:
        raise invalid("invalid Finding policy encoding") from None
    if (
        POLICY.dump_json(policy).decode() != payload
        or policy.emitted != min(CAP, policy.eligible)
        or policy.truncated != policy.eligible - policy.emitted
        or policy.eligible < 0
        or not policy.ordered_input_bindings
    ):
        raise invalid("Finding policy count or canonical encoding differs")
    return policy


def versions(result: ExchangeResult) -> tuple[str, ...]:
    return (
        (_policy(result.parts).extractor,)
        if any(p.role == "finding_policy" for p in result.parts)
        else ("graph.no_findings@v1",)
    )


def extract(
    descriptor: Descriptor,
    result: ExchangeResult,
    artifact_ref: str,
    committed_at: datetime,
    *,
    _validated: ValidatedDescriptor | None = None,
) -> tuple[t.GraphFinding, ...]:
    if not any(p.role == "finding_policy" for p in result.parts):
        return ()
    policy = _policy(result.parts)
    if policy.policy == "zero_findings@v1":
        if (
            policy.producer not in ("deviation.zscore", "deviation.mad", "time.runs")
            or policy.extractor != "graph.no_findings@v1"
            or (policy.eligible, policy.emitted, policy.truncated) != (0, 0, 0)
        ):
            raise invalid("deviation empty Finding authority differs")
        return ()
    if policy.producer.startswith(("association.", "forecast.")):
        from marivo.analysis.materialization.statistical_findings import (
            extract as extract_statistics,
        )

        return extract_statistics(descriptor, result, artifact_ref, committed_at, policy)
    raise invalid("Finding policy is attached to an ineligible producer")


def collection(
    store: SessionStore,
    conn: sqlite3.Connection,
    descriptor: Descriptor,
    artifact_ref: str,
    *,
    _validated: ValidatedDescriptor | None = None,
) -> tuple[tuple[t.GraphFinding, ...], t.GraphArtifactDigest]:
    artifact = conn.execute(
        "SELECT committed_at FROM dataset_artifacts WHERE artifact_ref=?", (artifact_ref,)
    ).fetchone()
    evidence = conn.execute(
        "SELECT * FROM dataset_evidence WHERE artifact_ref=?", (artifact_ref,)
    ).fetchone()
    if artifact is None or evidence is None:
        raise invalid("Artifact lacks atomic Evidence")
    committed_at = parse_timestamp(artifact[0])
    rows = conn.execute(
        "SELECT * FROM findings WHERE artifact_ref=? ORDER BY finding_ordinal", (artifact_ref,)
    ).fetchall()
    actual = []
    for ordinal, row in enumerate(rows):
        if row["finding_ordinal"] != ordinal:
            raise invalid("Finding ordered collection has a gap or duplicate")
        finding = decode_finding_body(
            row["finding_body_payload"],
            finding_id=row["finding_ref"],
            artifact_ref=artifact_ref,
            session_id=descriptor.signature.domain.binding.session_id,
            committed_at=committed_at,
        )
        actual.append(finding)
    findings = tuple(actual)
    try:
        version = TypeAdapter(tuple[str, ...]).validate_json(
            evidence["extractor_contract_versions_payload"], strict=True
        )
    except ValidationError:
        raise invalid("invalid Finding extractor version encoding") from None
    expected_versions = tuple(
        part.extractor for part in descriptor.signature.parts if isinstance(part, FindingPolicyPart)
    ) or ("graph.no_findings@v1",)
    if (
        evidence["finding_count"] != len(findings)
        or version != expected_versions
        or (version == ("graph.no_findings@v1",) and findings)
    ):
        raise invalid("Finding collection count or frozen extractor authority differs")
    return findings, t.GraphArtifactDigest(
        artifact_ref=ArtifactRef(ref=artifact_ref),
        quality_summary_digest=digest(encode(descriptor, DESCRIPTOR)),
        typed_issue_digest=digest("[]"),
        evidence_digest=evidence["evidence_digest"],
        finding_count=len(findings),
        finding_set_digest=evidence["finding_set_digest"],
        extractor_contract_versions=version,
    )


def encode_versions(value: tuple[str, ...]) -> str:
    import json

    return json.dumps(value, separators=(",", ":"))


def page(
    findings: tuple[t.GraphFinding, ...], artifact: str, limit: int, cursor: str | None
) -> t.GraphFindingPage:
    if type(limit) is not int or not 1 <= limit <= 100:
        raise invalid("Finding page limit must be an exact integer in 1..100")
    start = 0
    if cursor is not None:
        try:
            position, owner = decode_keyset_cursor(cursor)
        except (TypeError, ValueError):
            raise invalid("Finding cursor is malformed") from None
        if type(position) is not int or owner != artifact or not 0 <= position < len(findings):
            raise invalid("Finding cursor belongs to a different Artifact or collection")
        start = position + 1
    items = findings[start : start + limit]
    more = start + limit < len(findings)
    return t.GraphFindingPage(
        items, limit, more, encode_keyset_cursor(start + limit - 1, artifact) if more else None
    )
