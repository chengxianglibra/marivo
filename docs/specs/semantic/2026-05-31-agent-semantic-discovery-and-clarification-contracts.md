# Agent Semantic Discovery and Clarification — Implementation Contracts

Date: 2026-05-31

Status: draft implementation contract, pending approval. Companion to
`2026-05-31-agent-semantic-discovery-and-clarification-design.md`.

The design document owns the *why* and the architecture. This document owns the
*exact contracts to build*: the `decision_kind` taxonomy and materiality floors,
the confidence model, the concrete types and API signatures, the ledger on-disk
format, the structural fingerprint composition, and the `readiness` strict
enrichment floor. Where the design says "deferred to a follow-up spec," that
content lands here.

All Python files under `.marivo/semantic/<model>/` remain the only semantic source
of truth. Everything specified here is evidence, classification, persistence, or
advisory metadata.

## Scalar types

```python
from typing import Literal

Materiality = Literal["low", "medium", "high"]
# Ranking weight: low=1, medium=2, high=3 (see Ask Budget ranking).

AgreementConfidence = Literal["low", "high"]
# The agent's binary semantic-agreement verdict, AFTER the evidence-count floor
# is applied (the effective value the classifier rule consumes).

AuthorityLevel = Literal["establishes", "validates", "candidate_only"]

EvidenceType = Literal[
    "user_confirmation",  # establishes (highest)
    "knowledge",          # establishes
    "source_sql",         # establishes
    "comment",            # establishes (medium)
    "metadata",           # validates
    "sample",             # validates
    "structural",         # candidate_only (fact-shape, key-name match)
]

ObjectKind = Literal["dataset", "field", "time_field", "metric", "relationship"]
```

Authority mapping (matches the design's Evidence Authority Model):

| EvidenceType | AuthorityLevel | Ranking weight |
| --- | --- | --- |
| `user_confirmation` | establishes | 4.0 |
| `knowledge`, `source_sql` | establishes | 3.0 |
| `comment` | establishes | 2.0 |
| `metadata`, `sample` | validates | 1.5 |
| `structural` | candidate_only | 0.5 |

The numeric weights are the default scoring constants; they are tunable and live in
one module-level table, not scattered through call sites.

## 1. decision_kind taxonomy and materiality floors

```python
DecisionKind = Literal[
    "dataset_identity",
    "dataset_primary_key",
    "time_field_identity",
    "time_field_format",
    "time_field_granularity",
    "field_meaning",
    "amount_unit",
    "field_vs_metric",
    "metric_decomposition",
    "metric_additivity",
    "metric_exclusion_rule",
    "metric_provenance_status",
    "relationship_existence",
    "relationship_join_keys",
    "relationship_semantics",
    "equivalent_column_choice",
]
```

Floor table. `dangerous` is defined as `materiality_floor == "high"`. A dangerous
kind always yields a blocker when confidence is low and **never** carries a
non-`None` `default_if_unanswered` (no silent assumption-taking).

| decision_kind | materiality_floor | dangerous | default_if_unanswered allowed |
| --- | --- | --- | --- |
| `dataset_identity` | low | no | yes |
| `dataset_primary_key` | medium | no | yes |
| `time_field_identity` | high | yes | no |
| `time_field_format` | medium | no | yes |
| `time_field_granularity` | medium | no | yes |
| `field_meaning` | high | yes | no |
| `amount_unit` | high | yes | no |
| `field_vs_metric` | low | no | yes |
| `metric_decomposition` | high | yes | no |
| `metric_additivity` | medium | no | yes |
| `metric_exclusion_rule` | high | yes | no |
| `metric_provenance_status` | high | yes | no |
| `relationship_existence` | medium | no | yes |
| `relationship_join_keys` | medium | no | yes |
| `relationship_semantics` | high | yes | no |
| `equivalent_column_choice` | low | no | yes |

Effective materiality for a decision is `max(materiality_floor, agent_materiality)`
on the `low < medium < high` order; the agent may raise but never lower it.

`metric_provenance_status = "python_native"` is additionally an always-human
decision (design Tier 3): the library never auto-selects it; it requires an
explicit user confirmation recorded in the ledger.

## 2. Confidence model

Two distinct, separately named quantities — the design conflated them under one
word.

### candidate_confidence (Proposal Engine, mechanical)

A `[0, 1]` score used only to rank candidates. Deterministic.

```python
candidate_confidence = min(
    1.0,
    sum(weight(src.evidence_type) for src in backing_sources) / SATURATION,
)
# SATURATION default = 4.0. backing_sources are the Establishes/Validates sources
# attached to the candidate; candidate_only signals contribute their 0.5 weight
# but cannot alone exceed the "low" band.
```

### agreement_confidence (Classifier input, agent verdict + floor)

```python
def effective_agreement_confidence(
    agent_verdict: AgreementConfidence,
    qualifying_sources: int,
) -> AgreementConfidence:
    # qualifying_sources = number of DISTINCT EvidenceType values with
    # establishes/validates authority backing this slot. Independence is by type:
    # two comments on the same column count as one. candidate_only never counts.
    if agent_verdict == "high" and qualifying_sources >= 2:
        return "high"
    return "low"
```

This is the single-source-bluff guard: a "high" verdict with `< 2` qualifying
sources is downgraded to "low" and falls into the normal low-confidence branches of
the classification rule.

## 3. Core types

```python
from dataclasses import dataclass
from collections.abc import Callable, Mapping, Sequence
from typing import Any


@dataclass(frozen=True)
class EvidenceRef:
    evidence_type: EvidenceType
    authority: AuthorityLevel
    locator: str                 # e.g. "comment:orders.pay_status", "sql:kb://sales/revenue"
    excerpt: str | None          # short supporting snippet, redacted
    fingerprint: str             # sha256 of the cited evidence slice (see section 6)


@dataclass(frozen=True)
class Candidate:
    object_kind: ObjectKind
    proposed_id: str             # e.g. "sales.orders"
    decision_kind: DecisionKind
    slot_values: Mapping[str, object]  # proposed structural content only
    evidence: tuple[EvidenceRef, ...]
    candidate_confidence: float  # [0, 1], mechanical
    semantic_delta: str          # human-readable: what choosing this implies


@dataclass(frozen=True)
class Enrichment:
    # The agent's irreducibly-subjective inputs for one decision (design's
    # "agent-inferred" inputs). materiality is clamped to the floor; agreement is
    # clamped by the evidence-count floor.
    decision_kind: DecisionKind
    subject_ref: str
    materiality: Materiality            # raise-only; effective = max(floor, this)
    agreement_confidence: AgreementConfidence  # pre-floor verdict
    chosen: object | None = None        # the value the agent intends to author


@dataclass(frozen=True)
class OpenQuestion:
    id: str                      # stable; sha256(decision_kind, subject_refs, evidence_fingerprint)
    subject_refs: tuple[str, ...]
    decision_kind: DecisionKind
    gated_by: str | None         # the round-1 question whose answer created this one
    candidates: tuple[Candidate, ...]
    materiality: Materiality     # effective (post-floor)
    blast_radius: int
    agreement_confidence: AgreementConfidence  # effective (post evidence-count floor)
    default_if_unanswered: object | None       # None for dangerous floors
    severity: Literal["blocker", "optional"]
    blocker_reason: Literal["conflict", "high_materiality_low_confidence", "fail_closed", None]


@dataclass(frozen=True)
class DecisionRecord:           # per-object ledger entry
    decision_kind: DecisionKind
    chosen: object
    agreement_confidence: AgreementConfidence
    qualifying_sources: tuple[EvidenceType, ...]
    materiality: Materiality
    blast_radius: int
    evidence_fingerprint: str
    question_id: str | None     # set when the value was user-confirmed
    decided_at: str             # ISO-8601


@dataclass(frozen=True)
class RejectedCandidate:
    decision_kind: DecisionKind
    candidate: str
    reason: str
    evidence_fingerprint: str
    rejected_at: str


@dataclass(frozen=True)
class ConfirmationRecord:       # append-only confirmation log entry
    ts: str                     # ISO-8601
    question_id: str
    decision_kind: DecisionKind
    subject_refs: tuple[str, ...]
    answer: object
    evidence_fingerprint: str


@dataclass(frozen=True)
class RichnessGap:
    kind: Literal["coverage", "depth"]
    subkind: str                # e.g. "fact_table_no_metric", "missing_business_definition"
    refs: tuple[str, ...]
    demand_weight: float        # ranking key
    demand_evidence: tuple[str, ...]
    suggested_action: str


@dataclass(frozen=True)
class RichnessReport:
    gaps: tuple[RichnessGap, ...]   # ranked by demand_weight, descending
    checked_at: str


@dataclass(frozen=True)
class KnowledgeDoc:
    ref: str                    # e.g. "kb://sales/revenue"
    text: str
    dialect: str | None = None  # for SQL documents


@dataclass(frozen=True)
class KnowledgeBundle:
    documents: tuple[KnowledgeDoc, ...] = ()


@dataclass(frozen=True)
class DemandSignal:
    example_questions: tuple[str, ...] = ()
    intents: tuple[str, ...] = ()
    run_history_refs: tuple[str, ...] = ()
    build_purpose: str | None = None   # cold-start seed
```

`ReadinessReport`, `ReadinessIssue`, and friends already exist in
`marivo/semantic/readiness.py`; this spec extends them (section 7), it does not
redefine them.

## 4. API signatures

All methods hang off `SemanticProject`. Backend-bearing methods take a
`backend_factory` (a `Callable[[str], Any]` returning a live Ibis backend), never a
backend instance, matching the existing readiness/preview convention.

```python
def propose_candidates(
    self,
    *,
    datasource: str,
    tables: Sequence[str] | None = None,        # None = all reachable tables
    knowledge: KnowledgeBundle | None = None,
    backend_factory: Callable[[str], Any],
) -> tuple[Candidate, ...]:
    """Deterministic. Structural candidates only; no business meaning. Ranked by
    candidate_confidence, descending."""


def open_questions(
    self,
    refs: Sequence[str] | None = None,
    *,
    candidates: Sequence[Candidate] | None = None,
    enrichments: Sequence[Enrichment] | None = None,
    backend_factory: Callable[[str], Any],
    round_index: int = 0,
) -> tuple[OpenQuestion, ...]:
    """Runs the deterministic classification rule. Applies the materiality floor and
    the evidence-count floor to the supplied enrichments, computes blast_radius from
    the loaded graph, detects structural conflicts, then coalesces, ranks, and
    dedups against the ledger. round_index > 0 requires every returned question to
    carry gated_by."""


def answer(
    self,
    question_id: str,
    answer: object,
    *,
    rationale: str | None = None,
) -> None:
    """Records a ConfirmationRecord in the append-only log and updates the affected
    DecisionRecord. Confirmation evidence is highest authority."""


def readiness(
    self,
    *,
    strict_provenance: bool = False,
    strict_enrichment: bool = False,            # NEW (section 7)
    require_preview: bool = True,
    refs: Sequence[str] | None = None,
    backend_factory: Callable[[str], Any],
) -> "ReadinessReport":
    """Existing gate, extended. Reuses the open_questions engine and lifts
    unresolved high-materiality questions into unresolved_clarification blockers.
    Fail-closed on dangerous decisions lacking a ledger record."""


def audit(
    self,
    *,
    deep: bool = False,
    backend_factory: Callable[[str], Any],
) -> tuple[OpenQuestion, ...]:
    """Re-validation pass. Default (deep=False) re-checks structural fingerprints and
    re-surfaces decisions whose evidence changed. deep=True re-judges recorded
    multi-source agreement verdicts. Output flows through the same classifier path;
    dangerous re-surfaced decisions become unresolved_clarification blockers in
    readiness. Does not detect a misread over unchanged evidence."""


def richness(
    self,
    *,
    demand: DemandSignal | None = None,
) -> RichnessReport:
    """Pure advisory. Never blocks, never mutates readiness. Coverage gaps from the
    semantic graph + depth gaps from thin AiContext, ranked by demand_weight."""
```

## 5. Ledger on-disk format

Directory, project-local, committed to git for auditable provenance:

```text
.marivo/semantic/<model>/_evidence/
  objects/<semantic_id>.json     # one file per semantic object
  confirmations.jsonl            # append-only, one ConfirmationRecord per line
```

`objects/<semantic_id>.json`:

```json
{
  "semantic_id": "sales.revenue",
  "authored_at": "2026-05-31T10:00:00Z",
  "decisions": [
    {
      "decision_kind": "metric_decomposition",
      "chosen": "sum",
      "agreement_confidence": "high",
      "qualifying_sources": ["source_sql", "comment"],
      "materiality": "high",
      "blast_radius": 7,
      "evidence_fingerprint": "sha256:…",
      "question_id": null,
      "decided_at": "2026-05-31T10:00:00Z"
    }
  ],
  "rejected_candidates": [
    {
      "decision_kind": "time_field_identity",
      "candidate": "dt",
      "reason": "comment: partition load date",
      "evidence_fingerprint": "sha256:…",
      "rejected_at": "2026-05-31T10:00:00Z"
    }
  ]
}
```

Rules:

- JSON with sorted keys (canonical), one object file per `semantic_id`.
- The ledger contains no executable expression bodies. It is provenance only.
- A `git` diff of `_evidence/` shows *why* a semantic decision changed, alongside
  the `.py` diff that shows *what* changed.

## 6. Structural fingerprint composition

Single-tier, structural only (no sample values), per the design's accepted risk.

```python
def evidence_fingerprint(cited_columns, table_comment, column_comments) -> str:
    payload = {
        "columns": sorted(
            ({"name": n, "type": t} for n, t in cited_columns),
            key=lambda c: c["name"],
        ),
        "table_comment": table_comment,            # str | None
        "column_comments": dict(sorted(column_comments.items())),
    }
    return "sha256:" + sha256(canonical_json(payload)).hexdigest()
```

Scope rule: a decision's fingerprint covers **only the columns and comments the
decision cites** (from its `EvidenceRef.locator`s), not the whole table. Changing an
unrelated column does not re-stale a decision. Sample/preview values are
deliberately excluded — data drift is not caught here (design accepted risk).

## 7. readiness strict enrichment floor (Finding 3)

`readiness(strict_enrichment=True)` adds one blocker kind. The Richness Report is
unchanged and still never blocks; this floor is a `readiness` gate, not a richness
promotion.

- New `ReadinessIssue` kind: `missing_business_definition`.
- Scope: refs in the analysis-handoff set (`analysis_ready_refs` candidates).
- Rule: under `strict_enrichment=True`, any handoff ref with an empty
  `business_definition` is a blocker (`severity = "blocker"`,
  `kind = "missing_business_definition"`).
- `guardrails` absence is a `warning` under the same flag, not a blocker.
- Default `strict_enrichment=False`: the floor is opt-in, matching the existing
  `strict_provenance` style; long-tail richness remains advisory in all modes.

Rationale: the existing semantic spec uses `business_definition`/`guardrails` for
reuse and intent matching, so their absence on a ref being handed to analysis is a
usability hazard, not long-tail thinness.

## What this unblocks

With this contract, the Phase ordering in the design document becomes buildable:

1. **Single-session classifier + proposal engine** — sections 1, 2, 3, 4
   (`propose_candidates`, `open_questions`, `Enrichment`, the floor table, the two
   confidence functions). No ledger required; blast radius from the loaded graph.
2. **Ledger + cross-session features** — sections 5, 6, plus `answer`, `audit`, and
   the readiness fail-closed default.
3. **readiness strict enrichment floor** — section 7.
4. **Richness Report** — `richness`, `RichnessReport`, `RichnessGap`.

## Still tunable (not blocking)

- The numeric authority weights and `SATURATION` (section 2) are default constants
  in one table; they can be calibrated without changing any contract.
- The exact demand_weight formula for the Richness Report (section 3
  `RichnessGap.demand_weight`) — its inputs (`DemandSignal`) and output shape are
  fixed; the weighting curve is tunable.
