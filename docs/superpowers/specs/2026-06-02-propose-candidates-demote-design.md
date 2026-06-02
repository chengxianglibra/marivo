# Demote `propose_candidates` to a Non-Exhaustive Structural Scan

Date: 2026-06-02

Status: approved design, pending implementation plan.

## Context

`propose_candidates` (in `marivo/semantic/reader.py`, heuristics in
`marivo/semantic/proposal.py`) generates deterministic, structure-only candidates
from datasource metadata. Today it emits four object kinds:

- `dataset` (`dataset_identity`) — one per source.
- `time_field` (`time_field_identity`) — columns matching a temporal type/name regex.
- `field` (`field_meaning`) — columns matching an enum/status name regex.
- `relationship` (`relationship_join_keys`) — cross-table `<singular>_id` -> `id`
  key-name match.

It returns `tuple[Candidate, ...]`.

### Problem

The function is safe in the sense that candidates never auto-commit: they flow
into `open_questions` -> the classifier, where dangerous decisions become blockers
under low confidence. The real problems are about **coverage and framing**, not
safety:

1. **Anchoring / omission.** Returning a candidate list reads as "the candidates."
   An agent can treat it as the complete worklist and stop looking. The function
   proposes nothing for measures/metrics, `amount_unit`, `dataset_primary_key`, or
   plain (non-enum, non-temporal) dimension columns, so those get silently dropped.
2. **Weak coverage on high-value slots.** The omitted slots above are exactly the
   ones an agent most needs help noticing.
3. **False precision.** A `candidate_confidence` number attached to a regex guess
   can look more authoritative than it is.

### Decision

Keep the function name and the deterministic heuristics. Change the **return
contract** so a single call surfaces both the structural candidates and every
**residual column** the heuristics did not match. The agent reviews residuals and
judges them itself (measure / primary key / dimension / non-conventional FK). This
fits the project direction "provide the information, let the agent judge" without
discarding the deterministic, testable evidence-binding baseline that
`open_questions` consumes.

The decision layer (`open_questions`, classifier, ledger, readiness, richness) is
**unchanged**.

## New types

Both live in `marivo/semantic/proposal.py` and are exported from
`marivo.semantic`.

```python
@dataclass(frozen=True)
class ResidualColumn:
    dataset: str            # owning proposed dataset id, e.g. "sales.orders"
    column: str             # column name
    data_type: str          # raw type string, passed through verbatim
    nullable: bool | None
    comment: str | None     # column comment if present


@dataclass(frozen=True)
class ProposalResult:
    candidates: tuple[Candidate, ...]              # same order and content as today
    residual_columns: tuple[ResidualColumn, ...]   # columns no heuristic matched
```

`ProposalResult` is immutable, matching the repository's frozen-result convention.
The type name plus a non-empty `residual_columns` list communicates
non-exhaustiveness; no separate boolean flag is added.

## Coverage definition

A column is **covered** iff it is cited by a `time_field` or `field` candidate
(i.e. it matched the temporal or enum heuristic). Concretely, the covered set is:

```python
{c.slot_values["column"]
 for c in candidates
 if c.object_kind in ("time_field", "field")}
```

Every other column of each inspected source becomes a `ResidualColumn`.
Consequences, all intentional:

- Primary-key-like columns (`order_id`), measures (`amount`), and plain dimensions
  appear in `residual_columns`.
- `dataset` candidates do not "cover" specific columns.
- Columns used as relationship join keys **remain residual** — a join key may still
  warrant a dimension/field declaration, so the agent should still see it.

Residual columns carry no semantic guess: `data_type` and `comment` are passed
through verbatim. Judgment is the agent's.

## API change

`marivo/semantic/reader.py`:

```python
def propose_candidates(
    self,
    *,
    datasource: str,
    sources: Sequence[DatasetSourceIR],
    model: str,
    inspect_source: Callable[..., TableMetadata],
) -> ProposalResult:        # was: tuple[Candidate, ...]
    ...
```

Parameters are unchanged. The body inspects each source, builds candidates via
`candidates_from_metadata`, computes residuals via the new `residual_columns`
helper, appends cross-table `relationship_candidates`, and returns a
`ProposalResult`. The docstring is rewritten to state the result is a
non-exhaustive structural starting set and that callers must review
`residual_columns` for measures, primary keys, and dimensions.

## Implementation surface

- `marivo/semantic/proposal.py`
  - Add `ResidualColumn` and `ProposalResult` dataclasses.
  - Add a pure function:
    ```python
    def residual_columns(
        metadata: _TableMeta,
        candidates: Sequence[Candidate],
        *,
        model: str,
        source: DatasetSourceIR | None = None,
    ) -> tuple[ResidualColumn, ...]:
    ```
    It diffs `metadata.columns` against the covered set computed from
    `candidates`, building one `ResidualColumn` per uncovered column with
    `dataset` set to the qualified proposed dataset id. Residual columns
    preserve source column order (the order of `metadata.columns`) for
    deterministic output.
  - `candidates_from_metadata` and `relationship_candidates` are unchanged.
  - The import boundary is preserved: this module keeps using the `_TableMeta` /
    `_ColumnMeta` Protocols and does not import `marivo.analysis`.

- `marivo/semantic/reader.py`
  - `propose_candidates` returns `ProposalResult`; assemble residuals per source.
  - Rewrite the docstring (non-exhaustive framing).

- `marivo/semantic/__init__.py`
  - Import and re-export `ProposalResult` and `ResidualColumn`; add both to
    `__all__`.

## Documentation and skill changes

These carry the anti-anchoring intent and ship in the same change:

- `marivo-skills/marivo-semantic/SKILL.md`,
  `marivo-skills/marivo-semantic/references/workflow.md`,
  `marivo-skills/marivo-semantic/references/evidence-and-ledger.md`:
  state that `propose_candidates` returns structural signal only and is **not
  exhaustive**; add an explicit step to iterate `residual_columns` and decide which
  are measures / primary keys / dimensions worth declaring.
- `marivo-skills/marivo-semantic/references/examples/02_candidate_to_questions.py`:
  bind `result = project.propose_candidates(...)`, use `result.candidates`, and
  print `result.residual_columns` (the `orders` fixture should show `order_id` and
  `amount` as residuals).
- `docs/specs/semantic/2026-05-31-agent-semantic-discovery-and-clarification-contracts.md`
  §4: update the `propose_candidates` signature block to the `ProposalResult`
  return and the residual semantics, so the contract matches the implementation.

## Testing

- Unit (`residual_columns`): covered set is exactly the time_field/field cited
  columns; PK, measure, and relationship-join columns land in residual; each
  `ResidualColumn` carries the correct `dataset`, `column`, `data_type`,
  `nullable`, `comment`.
- Integration (`propose_candidates`): with the `orders` fixture
  (`order_id, dt, created_at, amount, status_code`),
  `result.candidates` contains `dataset`, `time_field(dt)`, `time_field(created_at)`,
  `field(status_code)`; `result.residual_columns` contains `order_id` and `amount`.
- Update existing tests:
  - `tests/test_semantic_open_questions.py` — read `.candidates` from the result.
  - `tests/test_semantic_agent_tightening.py` — workflow/skill text assertions for
    the reframed guidance (`project.propose_candidates(` substring still holds since
    the name is kept).

## Out of scope / rejected alternatives

- **Rename the function.** Rejected: the name is kept; reframing rides on the
  result type, docstring, and skill text. Avoids public-symbol churn across
  spec/skill/examples/tests.
- **Separate `uncovered_columns(...)` method.** Rejected: an opt-in second call the
  agent can forget does not eliminate anchoring; the single combined result does.
- **Emit residuals as low-confidence `field` candidates.** Rejected: floods
  `open_questions` with low-value questions and contradicts the structural-signal
  purpose.
- No changes to the classifier, ledger, readiness, or richness.
- Residuals do not receive any semantic inference; types and comments pass through
  unchanged.

## Blast radius

Code: `proposal.py`, `reader.py`, `semantic/__init__.py`.
Tests: `tests/test_semantic_open_questions.py`,
`tests/test_semantic_agent_tightening.py`, plus new residual tests.
Docs/skill: three skill docs, one example, one contracts spec section.
