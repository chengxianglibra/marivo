# marivo-semantic evidence and ledger

Semantic authoring is evidence-driven. Names are candidate signals only.

## Evidence categories

| Evidence | Agent responsibility |
| --- | --- |
| Project evidence | Load existing models, refs, descriptions, dependencies, and parity status. |
| Table metadata evidence | Run `mv.datasources.inspect_source(...)` for schema, comments, nullable flags, partition hints, and warnings. |
| Raw preview evidence | Run bounded previews for candidate tables, time-like columns, amount columns, enum/status columns, and join keys. |
| Knowledge evidence | Extract definitions, guardrails, synonyms, example questions, source SQL, and source documents. |
| User confirmation evidence | Ask only when evidence conflicts or cannot settle a business decision. |

`table.schema()` returns types but not comments.

## Ask the user

Ask when available evidence cannot settle a business decision:

- amount unit is unclear;
- status code meaning is undocumented;
- multiple time axes are plausible and no partition/business evidence settles them;
- source SQL and comments conflict;
- refund, cancellation, test-data, or exclusion rules are ambiguous;
- a no-source metric needs confirmation before using `verification_mode="python_native"`.

## Candidates and questions

`Candidate` objects are structural proposals. `OpenQuestion` objects are the
worklist of unresolved decisions.

```python
result = project.propose_candidates(
    datasource="warehouse",
    sources=[ms.table("orders")],
    model="sales",
    inspect_source=mv.datasources.inspect_source,
)
questions = project.open_questions(candidates=result.candidates)
# result.residual_columns: columns the heuristics did not match
# (measures, primary keys, dimensions, non-conventional FKs)
for rc in result.residual_columns:
    print("residual:", rc.dataset, rc.column, rc.data_type, rc.comment)
```

`result.residual_columns` lists every column the heuristics omitted. Iterate it and
decide which are measures, primary keys, or dimensions worth declaring. The candidates
list is **not exhaustive** — do not treat it as the complete worklist.

`project.open_questions(...)` is safe during cold start before `_model.py` is
authored. If the registry is not loaded, `OpenQuestion.blast_radius` is `0`; run
`project.reload()` successfully before closeout for real dependency impact.
`blast_radius` is a non-negative integer count of distinct transitive dependents.
Do not pass `subject_refs`, dependent ref tuples/lists, candidates, or evidence
objects to `DecisionRecord.blast_radius`.

## Confirmations

Use `project.answer(...)` only for user-confirmed answers to real
`OpenQuestion` objects. This appends a confirmation log entry and writes a
minimal object-level decision so readiness can recognize the answer after
reload. The answer must not be `None`.

```python
project.answer(question, "Use dt as the reporting time axis", evidence_fingerprint="sha256:...")
```

Confirmation records alone do not clear readiness. Readiness requires
object-level `DecisionRecord` entries. After authoring `@ms.metric` or
`@ms.time_field`, reload the project so Marivo auto-records the corresponding
`metric_decomposition` or `time_field_identity` authoring decision.

## Decision records

Use `project.record_decision(semantic_id, record)` only when a complete `DecisionRecord` can be
built from real question and evidence values, or to replace the minimal
user-confirmation decision with richer cited evidence. Do not invent internal fields.
`DecisionRecord.chosen` must not be `None`.
For `blast_radius`, use `question.blast_radius` or a dependency-graph count
computed by the project; never pass the refs themselves.

```python
from datetime import UTC, datetime
import marivo.semantic as ms

def decision_record_from_question(question, chosen, *, evidence_fingerprint, cited_source):
    evidence_types = sorted(
        {e.evidence_type for candidate in question.candidates for e in candidate.evidence}
    )
    return ms.DecisionRecord(
        decision_kind=question.decision_kind,
        chosen=chosen,
        agreement_confidence=question.agreement_confidence,
        qualifying_sources=tuple(evidence_types),
        materiality=question.materiality,
        blast_radius=question.blast_radius,
        evidence_fingerprint=evidence_fingerprint,
        question_id=question.id,
        decided_at=datetime.now(UTC).isoformat(),
        cited_source=cited_source,
    )
```

To record the decision, pass the semantic ID as the first argument and the
`DecisionRecord` as the second:

```python
record = decision_record_from_question(
    question,
    chosen,
    evidence_fingerprint="|".join(
        sorted(e.locator for candidate in question.candidates for e in candidate.evidence)
    ),
    cited_source={"datasource": "warehouse", "source": {"kind": "table", "table": "orders", "database": None}},
)
project.record_decision(question.subject_refs[0], record)
```

`semantic_id` comes from `question.subject_refs[0]` — the first object ref
the question targets.

Ledger records are provenance. They never replace `.marivo/semantic/<model>/*.py`
as semantic definitions.
