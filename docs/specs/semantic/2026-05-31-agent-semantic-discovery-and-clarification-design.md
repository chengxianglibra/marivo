# Agent Semantic Discovery and Clarification Design

Date: 2026-05-31

Status: draft design proposal, phased delivery, pending approval.

This document extends `docs/specs/semantic/agent-semantic-layer-authoring-design.md`.
That document defines the evidence-driven authoring loop and the readiness gate.
This one specifies four mechanisms that make the loop reach the target state:

> **few user clarifications + agent autonomous exploration -> a correct, usable,
> and rich semantic layer.**

The four mechanisms are:

1. **Candidate Proposal Engine** — turns fetched evidence into ranked structural
   candidates.
2. **Ambiguity Classifier and Ask Budget** — decides, per decision point, whether
   to auto-resolve, record an assumption, or ask the user; and bounds how much is
   asked.
3. **Evidence Ledger** — persists evidence, decisions, confirmations, and rejected
   candidates so build-once is reproducible, auditable, and incrementally
   improvable.
4. **Richness Report** — a demand-driven advisory that drives semantic richness
   without becoming an infinite nag.

Python files under `.marivo/semantic/<model>/` remain the only semantic source of
truth. Everything here is evidence, provenance, classification, or advice layered
around that source of truth.

## Why This Matters

Semantic layer construction is the foundation for whether every later
`marivo.analysis` call is accurate. It is built once and reused many times, and a
wrong semantic decision propagates silently into every future analysis. This
asymmetry justifies high investment in correctness up front.

Construction is agent-led with user clarification as a fallback. The system must
therefore bias toward asking rather than guessing on material decisions, while
keeping the number of questions small. Resolving that tension precisely — not with
a blanket "ask when unsure" — is the core problem this design solves.

## Design Principles

- **Build-once asymmetry.** Front-load rigor on the decisions that downstream
  analyses inherit. Blast radius (how many objects depend on a decision) is a
  first-class input, computed by code and never delegated to the agent.
- **Evidence-first, name-never.** Column and table names are candidate signals
  only. Business meaning comes from comments, source SQL, knowledge, bounded
  preview, or user confirmation. Evidence becomes a persisted, auditable artifact,
  not ephemeral in-session reasoning.
- **Agent-led, user-secondary.** Prefer closed confirmations ("I concluded X from
  evidence Y; correct?") over open clarifications. This preserves agent autonomy
  and lowers the user's load.
- **Correctness is gated; richness is advised.** Correctness is near-binary and
  belongs in the readiness gate. Richness has no objective ceiling and must never
  become a hard blocker.
- **Fail-closed on the dangerous path.** When required enrichment or ledger
  evidence is absent, dangerous decisions block rather than silently pass.
- **Clean separation of concerns.** Each mechanism owns one question and composes
  through well-defined surfaces rather than reaching into another's internals.

## Evidence Authority Model

The four mechanisms are machinery; this model is the decision content they run on.
It binds each evidence type to the object-content slots it is allowed to affect, at
a defined authority level, with a precedence order for conflicts. Without it, the
proposal engine's confidence and the classifier's auto/ask decision have nothing
principled to compute.

This model does **not** restate the per-object authoring rules — what a good
`business_definition` is, the time-axis selection priority, field-vs-metric, or
decomposition selection. Those remain owned by
`agent-semantic-layer-authoring-design.md` (its "Semantic Object Authoring
Contract", "Agent Decision Rules", and "Evidence Model" sections). This model is
the structured bridge between that evidence and this machinery.

### Authority levels

- **Establishes** — may set the business content of a slot on its own.
- **Validates** — may confirm or *refute* a slot's physical shape, but may not
  assert business meaning. A refutation is a structural conflict, not a proposal.
- **Candidate-only** — may suggest a candidate but never establishes anything
  alone.

### Evidence type -> authority

| Evidence type | Authority |
| --- | --- |
| User confirmation | Establishes any slot (highest) |
| Knowledge base / source SQL | Establishes `business_definition`, decomposition, exclusion/guardrail rules, time-axis identity |
| Table / column comments | Establishes meaning (medium authority) |
| Datasource metadata (schema, types, nullable, partition, keys) | Validates existence, type, grain; does not establish meaning |
| Sampled / preview data | Validates physical shape (units, enum values, formats, null behavior); may refute, never establishes business meaning |
| Discover / structural signals, column names | Candidate-only |

### Precedence and refutation

Conflict precedence, highest first:

```text
user confirmation > knowledge / source SQL > comments > metadata > sample > name
```

Two rules govern interaction:

- A **lower-authority source may refute a higher-authority one**, producing a
  structural conflict that must be asked — it never silently loses. (Sample data
  showing `pay_status in {0,1,2}` refutes a comment that says the column is
  boolean.)
- A **higher-authority source may not silently override a refutation** from a
  lower one. Overriding requires user confirmation, which then establishes the
  slot and is recorded in the ledger.

### How it feeds the machinery

- **Proposal engine confidence** = how many Establishes/Validates sources converge
  on a slot, weighted by authority.
- **Classifier `confidence` input** — the agent's semantic-agreement verdict is a
  judgment *within* this precedence; the evidence-count floor (Mechanism 2) counts
  Establishes/Validates sources, not Candidate-only ones.
- **Structural conflict detection** — fires exactly when a Validates source refutes
  an Establishes source, or two Establishes sources disagree. This is the
  top-priority blocker in the classification rule.

The exhaustive `decision_kind` taxonomy and per-kind materiality floor table that
sit on top of this model remain deferred to a follow-up spec.

### Content Derivation Tiers

Applying the authority model to the actual content slots of current semantic
objects (`@ms.dataset` / `@ms.field` / `@ms.time_field` / `@ms.metric` /
`ms.relationship` and `AiContext`) sorts them into three tiers. The dividing line:
physical shape is library-derivable; business meaning is derivable only when
documentary evidence exists and converges; conflict resolution and trust decisions
are always human.

**Tier 1 — library-derived, no user.** Physically determined or sample-validated;
for these slots, Validates *is* establishment because no business meaning is
involved.

| Slot | Source |
| --- | --- |
| object `name`, dataset `datasource` | discover / known context |
| dataset body (`ibis.table` + schema physical access) | metadata |
| field body of a bare column access (`orders.amount`) | metadata |
| time_field `data_type` | schema type |
| time_field `date_format` / `required_prefix` (string/integer time) | sampled preview values |
| relationship join-key shape / cardinality viability | sampled join keys |
| dataset `primary_key` (candidate + uniqueness sample) | key metadata + sample |
| metric `declared_status` -> *verified* | library `parity_check(source_sql)` |
| `source_sql` / `source_dialect` / `source_document` / `source_notes` | user-supplied knowledge (ingested, not a separate confirmation) |

**Tier 2 — documentary-establishable, else confirm.** Business-meaning slots.
Comments / knowledge / source SQL (all Establishes-authority) derive them when
present and convergent; the user is asked only when evidence is absent, ambiguous,
or conflicting. This is where the "few clarifications" tension actually lives.

| Slot | Established from (when present) | Must ask when |
| --- | --- | --- |
| `business_definition` | comments / knowledge | no documentary evidence |
| `guardrails` / exclusion rules (refunds, test data) | knowledge / source SQL filters | rule undocumented |
| time_field identity (which column is the business axis) | knowledge / SQL / partition (4-level priority) | several axes plausible, no documentary tiebreak |
| time_field `granularity` | knowledge / default | material and uncertain |
| metric `decomposition` (sum / ratio / weighted_average) | formula / source SQL / existing components | unprovable (never default to sum) |
| amount unit (cents / dollars / gross / net) | comments / knowledge (sample may *refute*, not establish) | undocumented |
| status / enum meaning (`status = 1` is paid) | comments / knowledge (sample gives values, not meaning) | undocumented |
| metric `additivity` | knowledge / metric nature | material and uncertain |
| relationship business intent + cardinality semantics | knowledge / comments | unconfirmed |

**Tier 3 — always human.** Cannot be fetched from system evidence.

| Slot / decision | Why human |
| --- | --- |
| resolving any source conflict (sample refutes comment; SQL contradicts comment) | authority model: overriding a refutation requires user confirmation |
| metric `declared_status = "python_native"` (no source-SQL oracle) | trust/authorization decision with no mechanical adjudication |
| business intent with zero documentary evidence and ambiguous candidates | not fetchable |

The `AiContext` richness slots — `synonyms`, `examples`, `instructions`,
`owner_notes` — sit in none of these gates. The agent may propose them from the
`business_definition` and domain knowledge; the Richness Report drives them by
demand; the user enriches optionally. They are advisory, not a confirmation gate.

Minimizing Tier 2's question count is exactly the job of the Evidence Authority
Model, the Proposal Engine, and the Ambiguity Classifier together.

## Mechanism 1: Candidate Proposal Engine

### Purpose

Automate "autonomous exploration" by turning fetched evidence (datasource
metadata, table and column comments, bounded preview, ingested knowledge) into
ranked candidate semantic objects that the agent ratifies, edits, or rejects.

### Contract

- **Structure only, never meaning.** The engine proposes physical/structural
  candidates: fact-shaped tables -> candidate datasets, time-like columns ->
  candidate time fields, enum/status/code columns -> candidate fields, matching
  key shapes -> candidate relationships. It attaches a mechanical confidence and
  the backing evidence bundle to each candidate. It **never** produces
  `business_definition`, `decomposition`, amount-unit decisions, or any other
  business semantics — those are the agent's responsibility, supplied from
  comments, knowledge, or user confirmation.
- **Positive candidates only.** The engine proposes objects for structures that
  exist. It does **not** detect "negative space" (structures that should be
  modeled but are not). Coverage gaps are owned by the Richness Report.
- **Output composes as classifier input.** A proposed candidate is exactly a
  decision point with `default = the proposed candidate`. The Ambiguity Classifier
  consumes engine output directly; no separate ratification machinery is needed.

This preserves the existing non-goal: do not infer business definitions from names
or preview rows. The engine proposes *shape*; it never asserts *meaning*.

## Mechanism 2: Ambiguity Classifier and Ask Budget

### Purpose

Turn "asking the user" from an ad hoc agent behavior into a bounded, ranked,
deduplicated, and measurable resource, with the dangerous inputs kept out of the
agent's hands.

### The three inputs

| Input | Source | Constraint |
| --- | --- | --- |
| `blast_radius` | Code, from the dependency-graph transitive closure | Agent cannot influence it |
| `materiality` | Agent | Code sets a per-`decision_kind` floor; the agent may raise it but never lower it |
| `confidence` | Agent's semantic-agreement verdict over the evidence | Constrained by an evidence-count floor: a high-confidence claim backed by a single source is auto-downgraded |

### Classification rule (deterministic, code-executed)

```text
structural conflict (column missing / self-contradiction / dialect mismatch)
    -> blocker, highest priority, overrides everything else
high materiality + low confidence
    -> blocker, no handoff until answered
low materiality + low confidence
    -> take a conservative default, record an assumption + warning
high confidence backed by >= 2 independent evidence sources
    -> auto-decide, record evidence
```

The classification rule itself is code. Only one of its inputs (`materiality`) is
agent-supplied, and it is floored. The agent cannot bypass the rule by feeling
confident or by deeming something unimportant.

**Conflict outranks missing.** Silently resolving a contradiction between evidence
sources is the most dangerous action under build-once — more dangerous than a
missing source — because it looks justified. Structural conflict is therefore the
top-priority blocker, independent of materiality and confidence.

### Anti-gaming guards

Three escape routes exist for an agent that wants to avoid asking, or to dump
everything on the user:

- **"Mark it unimportant"** — closed by the materiality floor. Dangerous
  `decision_kind`s (time-axis selection, amount unit, decomposition type,
  exclusion rules, status-code meaning) carry a hard high floor that the agent
  cannot lower.
- **"Single-source bluff"** — closed by the evidence-count floor. Code counts how
  many independent Establishes/Validates sources are present per the Evidence
  Authority Model (Candidate-only signals such as column names do not count; code
  need not judge agreement). A high-confidence verdict with one qualifying source
  is downgraded.
- **"Multi-source misread as agreeing"** — *accepted as residual risk.* When two
  or more sources are present, the agent's semantic-agreement verdict is trusted
  without an extra user confirmation. This risk is *reduced* — not eliminated — by
  the ledger audit pass, which re-validates the verdict when the underlying
  evidence later changes or on an explicit re-audit; a misread over *unchanged*
  evidence is not caught. (The alternative — a forced confirmation on
  high-blast-radius decisions regardless of confidence — was considered and
  rejected to protect the "few clarifications" goal.)

### Ask budget

- **Blockers have no cap.** Wrong semantics are never shipped to save a question.
- **Optional confirmations have a soft top-K cap.** "Optional" means an item the
  rule did *not* mark as a blocker — an auto-decided or assumption-eligible item
  the agent surfaces for a sanity check. Beyond K, these degrade to recorded
  assumptions. A `high materiality + low confidence` item is a blocker, never
  optional, and is never degraded to an assumption.
- **Assumption-taking is forbidden on dangerous floors.** `default_if_unanswered`
  may be non-`None` only for non-dangerous `decision_kind`s. A dangerous floored
  kind always yields a blocker (`default_if_unanswered = None`) and can never be
  silently assumption-taken. "Resolvable" therefore never includes a dangerous
  floored decision.
- **Each round the user sees only:** all blockers, the top-K optional
  confirmations, and a one-line summary ("N assumptions taken; see readiness").
- **Ranking:** `materiality x blast_radius`.
- **Coalescing key:** `(decision_kind, physical entity)`. One status column's
  meaning is one question, regardless of how many metrics depend on it.
- **Dedup:** by evidence fingerprint, with a cross-session interface backed by the
  ledger.
- **Confirm over clarify:** prefer closed `"I concluded X; correct? [X / Z /
  other]"` over open `"what does status = 1 mean?"`.

### Batching and rounds

- **Single batch is the default and the target.** Collect all open questions across
  the authoring pass and present them once.
- **Follow-up rounds are the exception** and must be code-justified. Every
  second-round decision point must carry a `gated_by` field pointing to the
  first-round question whose answer structurally created it (e.g., confirming a
  metric is gross *creates* a refund-exclusion question; confirming net removes
  it). A second-round question without `gated_by` is an agent omission and is
  flagged as such — it should have appeared in round one. Round count is not
  hard-capped; the `gated_by` invariant makes it converge in two to three rounds.

### Surfaces: one engine, two faces

`open_questions` and `readiness` are two moments over one classifier engine:

- `project.open_questions(...)` exposes the classifier during authoring so the
  agent knows what to ask before and while it writes objects.
- `project.readiness(...)` reuses the same engine at closeout and lifts unresolved
  high-materiality questions into a new `unresolved_clarification` blocker kind.

The two must never drift, so they share one implementation. When `readiness` runs
on a model with no enrichment and no ledger record, dangerous-kind decision points
are in a "floored-high, no recorded confidence" state and therefore **fail-closed
to blockers**. An unaudited model is blocked on its dangerous decisions until
enrichment or confirmation evidence exists.

### Output contract

```python
@dataclass(frozen=True)
class OpenQuestion:
    id: str                      # stable; used for cross-session dedup
    subject_refs: tuple[str, ...]
    decision_kind: str
    gated_by: str | None         # the round-1 question that created this one
    candidates: tuple["Candidate", ...]  # each carries evidence + the semantic delta of choosing it
    materiality: str
    blast_radius: int
    default_if_unanswered: object | None  # the fallback assumption, or None for a hard blocker
```

The user's answer is written back to the ledger as confirmation evidence.

## Mechanism 3: Evidence Ledger

### Purpose

Persist what was inspected, what was decided, why, with what confidence, and what
the user confirmed — so build-once is reproducible, auditable, incrementally
improvable, and free of cross-session re-asking.

### Contract

- **Provenance metadata, not a second DSL.** The ledger never contains executable
  expression bodies and never becomes a parallel semantic definition. Python files
  remain the only semantic source of truth.
- **Location:** `.marivo/semantic/<model>/_evidence/`, project-local under
  `.marivo/` per the repository state rules.
- **Two record types:**
  - **Per-object evidence record** — answers "why is `sales.revenue` defined this
    way" with the evidence and confidence at author time.
  - **Append-only confirmation log** — answers "what did the user tell us, and
    when," timestamped. Confirmation evidence is a higher trust class than agent
    inference and is the source of cross-session dedup.
- **Records rejected candidates and reasons.** "`dt` was considered as the time
  axis and rejected because the comment marks it as the partition load date." This
  prevents a future agent from re-exploring or re-asking a settled path. The cost
  is a larger ledger; this is accepted because it directly serves the "few
  clarifications" goal.

### Staleness: single-tier structural fingerprint

A decision's backing evidence is fingerprinted by hashing **schema (column names +
types) and table/column comments**. When the fingerprint changes, decisions that
depend on that evidence are marked stale and re-surface as questions.

- The structural fingerprint is checked on every load, with no backend access and
  effectively zero cost.
- **Accepted risk:** data-side semantic drift is *not* auto-detected. A new
  `pay_status = 2` value or a changed date format does not change the structural
  fingerprint, so a prior confirmation ("status = 1 means paid") stays valid as
  long as the column and comment are unchanged. The system trusts that value
  semantics are stable once confirmed; the backstop for data drift is the manual
  re-audit and the Richness Report, not the fingerprint. (A two-tier fingerprint
  that also samples value distributions on backend-bearing `readiness` runs was
  considered and rejected to keep every-load checking free.)

### Ledger audit pass

The audit pass re-validates recorded decisions against their evidence. It is the
re-validation half of the staleness mechanism, not a separate subsystem.

- **Trigger.** On `project.load()` for the structural fingerprint (zero backend
  cost); on an explicit `project.audit(...)` for a deeper re-judgment.
- **Checks.** (1) decisions whose structural fingerprint changed since author
  time; (2) on explicit audit, recorded multi-source agreement verdicts re-judged
  against current evidence.
- **Output.** Affected decisions are re-surfaced as `OpenQuestion`s through the
  same classifier path; dangerous-kind decisions become `unresolved_clarification`
  blockers in `readiness`. The pass does not introduce a new gate — it reuses the
  classifier and the readiness gate.
- **Honest scope.** The pass catches a misread only when the underlying evidence
  later changes, or when an explicit re-judgment is run. It does *not* silently
  detect a wrong agreement verdict over *unchanged* evidence; that case is the
  accepted residual risk, not something the audit pass removes.
- **Phasing.** Depends on the Evidence Ledger; lands with the cross-session
  features.

### What the ledger underpins

- Cross-session dedup for the Ask Budget.
- The `readiness` fail-closed behavior (no ledger record for a dangerous decision
  => blocker).
- The audit pass that re-validates "multi-source misread" decisions from
  Mechanism 2 when evidence changes or on explicit re-audit (it reduces, not
  eliminates, that risk).

## Mechanism 4: Richness Report

### Purpose

Drive semantic richness — the "rich" goal — without coercion and without an
infinite nag, given that richness has no objective ceiling.

### Two dimensions

- **Coverage (breadth).** The negative space the proposal engine deliberately
  skips: a fact table with no metric, time-like columns with no declared time
  field, datasets that share keys with no declared relationship, a datasource
  table never modeled. Mechanically detectable from metadata and the semantic
  graph.
- **Depth (per-object quality).** Objects that exist but have thin AI context: no
  `business_definition`, no `guardrails`, no `synonyms`, no `examples`,
  undocumented status codes. Field presence is mechanical; whether the content is
  *good* is judgment.

### Demand-driven termination

Richness is measured against actual or anticipated **demand**, not against a
maximization target. A metric that is never queried does not need ten synonyms; a
metric central to many analyses needs thorough guardrails. A table nobody wants to
query is not a coverage gap; a table that analysis wants to query but has no metric
is a real gap.

Demand signals come from `ai_context.examples` (natural-language questions),
analysis intents, and run history. This is what bounds richness: you are rich
*enough* when you cover the demand, not when you have exhausted every field.

**Cold start.** On a freshly built model with no history and no intents, the seed
demand is the agent's stated build purpose and target questions — the semantic
layer is being built *for* some analysis goal. The demand signal thickens as real
usage accumulates.

### Pure advisory

- The Richness Report **never blocks and never promotes findings into
  `readiness`.** All gating stays in `readiness`, independently — including the
  case of a demand-bearing metric handed to analysis with no `business_definition`.
  This case is not mere long-tail thinness: the existing semantic spec relies on
  `business_definition` and `guardrails` for reuse and intent matching, so their
  absence on a handoff ref is a genuine usability hazard. Keeping it advisory-only
  is a deliberate trade (see Accepted Risks). If a minimal enrichment floor is
  wanted, its correct owner is `readiness` under strict mode, not this report — and
  that floor is currently out of scope here.
- Because it has no teeth, its power comes entirely from **placement and
  ranking**: it appears at the authoring closeout (so it is unmissable) and ranks
  gaps by demand weight (so the highest-value gaps float to the top).
- **Accepted posture:** both semantic richness and the data-drift backstop that
  the structural fingerprint omits ultimately depend on this advisory being acted
  upon. The system chooses prioritized visibility over coercion.

### Output

A prioritized report parallel to but independent from `ReadinessReport`: demand-
weighted ranked gaps, each with its demand evidence and a suggested action.

## How the Four Mechanisms Compose

```text
inspect ─► Proposal Engine (positive structural candidates) ─► candidates == DecisionPoints
                                                                      │
                  ┌────────────────────────────────────────────────── ┘
                  ▼
        Ambiguity Classifier + Ask Budget ──► batched confirmation (few) ──► author .py objects
                  │                                                              │
                  └──────────────► Evidence Ledger (substrate) ◄────────────────┘
                                    │  records evidence / confirmations / rejected candidates
                                    │  structural fingerprint guards staleness
                                    ▼
                                readiness (gate) ──► handoff to analysis
                                    │
                                Richness Report (pure advisory, demand-driven)
```

Mapping back to the goals:

| Goal | Delivered by | Status |
| --- | --- | --- |
| **Correct** | existing preview / parity / readiness + **Ambiguity Classifier** (stops silent wrong semantics) | partly new |
| **Usable** | existing readiness handoff gate | existing |
| **Rich** | **Richness Report** (advisory) + **Proposal Engine** (proposes coverage candidates) | new |
| **Few clarifications** (orthogonal) | **Ambiguity Classifier + Ask Budget + Evidence Ledger** (cross-session dedup) | new |

The Evidence Ledger is the substrate that ties it together: it makes "few" hold
across sessions, makes `readiness` fail-closed, gives the Richness Report its
demand signal, and gives the classifier its audit pass (which re-validates trusted
verdicts when evidence changes, rather than eliminating the misread risk).

## Accepted Risks

- **Multi-source misread.** With two or more evidence sources present, the agent's
  semantic-agreement verdict is trusted without a user confirmation. The ledger
  audit pass re-validates the verdict when the underlying evidence later changes or
  on an explicit re-audit, but a misread over *unchanged* evidence is not caught.
  This is the deliberate cost of choosing the evidence-count floor (guard A) over a
  forced high-stakes confirmation.
- **Data-side drift.** The single-tier structural fingerprint does not detect new
  enum values or format changes. Backstopped by manual re-audit and the Richness
  Report.
- **Advisory richness.** Richness is never enforced; it relies on a prioritized
  advisory being acted upon. In particular, a demand-bearing metric handed to
  analysis with no `business_definition` or `guardrails` is a usability hazard left
  advisory-only here; closing it would require a minimal enrichment floor in
  `readiness` (strict mode), which is deliberately out of scope for this design.

Each risk was a deliberate trade in favor of the "few clarifications" and low-cost
goals.

## Non-Goals

- No second semantic DSL. The ledger is provenance, never executable definition.
- No business meaning inferred by code. The proposal engine proposes structure
  only.
- No richness blockers. Richness never gates handoff.
- No negative-space detection inside the proposal engine. Coverage gaps belong to
  the Richness Report.
- No forced high-stakes confirmation. The confidence guard is the evidence-count
  floor only.

## Phasing and Dependencies

- The **Evidence Ledger** is the substrate; cross-session dedup, the confidence
  audit pass, and persistence of enrichment and confirmation evidence depend on
  it. The classifier's single-session behavior (in-memory evidence, blast radius
  from the loaded graph) can land before the ledger; cross-session features land
  with it.
- The **Ambiguity Classifier** shares one engine across `open_questions` and
  `readiness`; the `unresolved_clarification` issue kind and the fail-closed
  default are added to the existing readiness aggregation.
- The **Proposal Engine** output is shaped as classifier input from the start.
- The **Richness Report** is independent of `readiness` and can land last.

## Left for Follow-up Specs

- The `decision_kind` taxonomy and the per-kind materiality floor table (e.g.,
  time-axis / amount-unit / decomposition / exclusion = high floor;
  field-vs-metric / equivalent-column-choice = low).
- The candidate confidence scoring formula (mechanical signals -> score).
- The ledger file format and on-disk schema.
- The structural fingerprint hash composition.

## Acceptance Criteria

This design is successful when:

- the proposal engine produces structural candidates that flow directly into the
  classifier as decision points;
- dangerous decisions cannot be auto-resolved by *unconstrained* agent self-
  assessment: they are floored to high materiality and require >= 2 independent
  evidence sources, and a single-source verdict is downgraded — with the
  multi-source-misread case acknowledged as a residual risk (see Accepted Risks),
  not claimed to be eliminated;
- the number of *optional* user-facing confirmations per authoring pass is bounded,
  ranked, and deduplicated; blockers are unbounded but coalesced, ranked, and never
  suppressed or capped;
- the evidence ledger makes a build reproducible and prevents cross-session
  re-asking of settled decisions;
- an unaudited model fails closed on its dangerous decisions in `readiness`;
- the richness report drives coverage and depth against demand without ever
  blocking handoff;
- Python semantic files remain the only semantic source of truth.
