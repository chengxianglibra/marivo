# marivo-semantic evidence and ledger

Semantic authoring is evidence-driven. Names are candidate signals only.

## Evidence categories

| Evidence | Agent responsibility |
| --- | --- |
| Project evidence | Load existing models, refs, descriptions, dependencies, and parity status. |
| Table metadata evidence | Run `mv.datasources.inspect_table(...)` for schema, comments, nullable flags, partition hints, and warnings. |
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
- a no-source metric needs confirmation before using `declared_status="python_native"`.

## Candidates and questions

`Candidate` objects are structural proposals. `OpenQuestion` objects are the
worklist of unresolved decisions.

```python
candidates = project.propose_candidates(
    datasource="warehouse",
    tables=["orders"],
    model="sales",
    inspect_table=mv.datasources.inspect_table,
)
questions = project.open_questions(candidates=candidates)
```

`project.open_questions(...)` is safe during cold start before `_model.py` is
authored. If the registry is not loaded, `OpenQuestion.blast_radius` is `0`; run
`project.reload()` successfully before closeout for real dependency impact.

## Confirmations

Use `project.answer(...)` for user-confirmed answers:

```python
project.answer(question, "Use dt as the reporting time axis", evidence_fingerprint="sha256:...")
```

## Decision records

Use `project.record_decision(...)` only when a complete `DecisionRecord` can be
built from real question and evidence values. Do not invent internal fields.

```python
from datetime import UTC, datetime
import marivo.semantic as ms

def decision_record_from_question(question, chosen, *, evidence_fingerprint, cited_table):
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
        cited_table=cited_table,
    )
```

Ledger records are provenance. They never replace `.marivo/semantic/<model>/*.py`
as semantic definitions.
