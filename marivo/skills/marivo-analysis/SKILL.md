---
name: marivo-analysis
description: Use when a user wants to investigate a business, product, or operational question with trusted data in Marivo, such as understanding what happened, comparing performance, explaining a change, evaluating confidence in the evidence, anticipating what may happen next, or continuing an earlier investigation.
---

# marivo-analysis

## Role and authority

Use this skill as a workflow and boundary kernel, not as an API manual or a
fixed analysis recipe. Trust the verified installed Marivo environment:

- `marivo.help("analysis.<target>")` owns static signatures, constraints,
  examples, return types, and error contracts;
- governed semantic objects own business meaning;
- `.show()` owns bounded current state;
- `.contract()` owns mechanically valid next actions;
- structured errors own repair guidance.

Do not replace these sources with remembered syntax or private implementation
details.

## Start with evidence, not administration

After invoking this skill, prefer to establish the environment fingerprint,
read the relevant focused live help, enter one session, resolve the required
semantic inputs, and run the first bounded observation early. Keep the question
coverage checklist as comments or working state in the main script. The live
help surface owns the exact session, catalog, and operator calls; this skill
does not reproduce those API recipes.

Choose catalog families from the question. Ordinary catalog-metric analysis
needs only `metrics` and `dimensions`, plus `time_dimensions` when time is part
of the question. If paths are unknown, prefer showing those required
collections together once.

## Minimal analysis flow

For an ordinary investigation, use this bounded sequence as the default path:

1. Decompose the question into a short coverage checklist.
2. Select the project interpreter, run `<selected-python> -m marivo doctor`
   once, and keep using that interpreter.
3. Read the focused live help for the capability you need.
4. Create or resume one question-scoped session.
5. Resolve all required typed `metric`, `dimension`, and, when time is involved,
   `time_dimension` references together.
6. Run one minimal bounded `observe` before optional exploration. If the
   question does not name periods, use the smallest useful time observation to
   identify available and complete comparable periods.
7. Use the returned artifact to confirm the period, completeness, and metric
   definitions that govern the analysis.
8. Use the closed `runtime_metric` route only for a question-scoped expression
   over governed inputs. Hand a missing reusable business definition to
   `marivo-semantic`, then resume the affected branch after readiness.
9. Perform only the necessary terminal decomposition after a typed artifact has
   established the semantic inputs and evidence scope.
10. Run the focused `assess_quality` flow for the material result, following
    its live-help input contract.
11. Answer the requested questions first, with the evidence, interpretation,
    caveats, and blockers needed to support the conclusion.

Prefer reaching the first bounded observation before optional exploration or
building the full terminal analysis script.

## Enter the environment once

Prefer the project interpreter provided by the host. For a conventional
project-local installation, prefer `.venv/bin/python` on macOS, Linux, and WSL,
or `.venv/Scripts/python.exe` on Windows.

Prefer running `<selected-python> -m marivo doctor` once before analysis and
using the reported Marivo version, executable, package path, and project state
as the environment fingerprint. Keep using that interpreter for live help and
execution. Use `<selected-python> -m marivo help` when the environment
fingerprint or global entry map is useful.

For session entry and operator call shapes, prefer the focused analysis help
selected for the question.

## Resolve semantic inputs through live help

Use the question and any semantic handoff to identify the required object
families. Start with the relevant focused analysis help when the operator or
input family is known; use the analysis catalog help only when the required
semantic identity or collection is not already known. The focused live help
owns the collection names, lookup grammar, entry details, reference handoff,
readiness call, and operator signature.

Resolve all required inputs together in the main script. Browse only the
question-relevant collection when an identity is unknown, and treat multiple
matches as an ambiguity to resolve rather than a reason to enumerate unrelated
semantic families. If an exact typed identity is already available, follow the
live help handoff directly without exploratory browsing.

When the requested calculation is question-scoped and fully expressible from
governed inputs, consult `marivo.help("analysis.runtime_metric")` and follow its
closed expression contract. A missing reusable organizational definition or
disputed business meaning requires an immediate semantic-authoring handoff.
Resume the affected analysis branch from the returned `analysis_ready_inputs`.

Do not repeat readiness when the current handoff already attests that the
selected project and inputs are analysis-ready. Otherwise follow the focused
live help contract for one readiness check over only the selected inputs.

## Plan from the question

Before running analysis, turn the request into a short coverage checklist. For
each required answer, record:

- the decision or claim to support;
- its population, time scope, metric, and comparison or grouping;
- the minimum evidence needed;
- whether the answer is observed, interpreted, or still unsupported.

When the question asks for an adjusted, marginal, or incremental effect, make
the comparison direction explicit in the checklist. Fit and report the baseline
that excludes the focal input, the model that adds it after the stated controls,
and the focal increment in fit or outcome; do not substitute the reverse
comparison unless the user asked for it.

Keep this checklist question-shaped, not operator-shaped. Do not add unrelated
analyses merely because an affordance exists. Resolve only the semantic inputs
needed by the checklist; avoid broad catalog, help-target, or artifact
enumeration.

Carry an explicitly selected cohort, top-N set, segment, or time window into
downstream questions about "these" items. Do not substitute a broader
population for the requested subset; label any broader sensitivity analysis as
secondary. When the downstream question asks for a numeric association, compute
and report the chosen statistic on exactly that subset before any broader
sensitivity; a qualitative judgment or full-population statistic is not a
substitute.

For a multi-factor relationship question, give every named factor a checklist
row and cover both its directly requested relationship and any joint adjustment
the conclusion relies on. Do not present scattered pairwise results as a
complete relationship set when a named pair or matrix cell is missing. When
multiple numeric factors are presented as a relationship set, show every
requested pair or a complete matrix rather than only each factor's relationship
with the outcome.

Treat words such as "each", "every", and "all" as explicit coverage
requirements. Before closeout, check the required entity × period/comparison ×
measure grid and either support every cell or mark the missing cells. For a
multi-period trend, cover every adjacent interval needed to establish the
reported pattern; do not let an endpoint summary hide an unsupported middle
period. Put requested exhaustive coverage in the deliverable, not only in a
script or internal checklist; consolidate it into a table when appropriate.
If the request asks what to change for every entity or category, include one
action or an explicit evidence-based "no change" decision for every row.

## Execute in bounded passes

1. Resolve exact governed inputs and confirm readiness for the required scope.
2. Read focused help only before the first unfamiliar public capability or
   when a structured error directs you there.
3. Write one append-only session-local script for the current decision round,
   batching compatible calls and only the small calculations it needs. After
   execution, do not edit that script.
4. In a later round, restore exact input artifacts by ref; do not import or
   re-execute an earlier script. Show only artifacts produced in the current
   round. Read `.contract()` only when the next valid continuation is unknown.
5. After a failure, follow one structured repair in a new step script.
6. Update the coverage checklist from current artifacts and stop when every
   required answer is supported or explicitly blocked.

Before optional exploration, write or update the required deliverable from the
supported findings. Continue only when one additional result could materially
change a required conclusion, recommendation, or limitation. Prefer one
well-supported answer over many weakly connected cuts.

Keep the coverage checklist as working state unless the user asks for an audit.
Do not turn environment checks, artifact inventories, or script bookkeeping
into report sections.

## Execution budget

Batch compatible catalog resolutions and observations instead of probing them
one at a time. Prefer focused live help, governed object state, artifact
contracts, and structured errors for discovery and continuation guidance.

Reserve the final third of the available turns—or the final ten tool calls when
no turn budget is known—for required derivations, reconciliation, consistency
checks, and the deliverable. Once that reserve begins:

- do not open a new semantic scope, optional drilldown, or method family;
- remove optional refinements before dropping a required answer;
- update the deliverable immediately, then fill only explicit checklist gaps.

Design terminal calculations with the simplest defensible method that answers
the question. After any two failed executions of the main script, freeze scope
and replace or remove the fragile component instead of continuing a patch
chain. Do not spend the delivery reserve polishing a fragile method while
supported core results remain unwritten.

## Deterministic recovery and stop rules

Treat failures as the same root cause when the structured error kind, failed
capability, and rejected semantic or artifact condition are unchanged.

- After the first failure, apply its structured repair or one focused-help
  recovery.
- If the same root cause occurs twice, stop that branch and disclose the gap.
- Do not repeat a successful observation solely to obtain a new artifact id or
  differently formatted output.

Inspect historical sessions only when resuming work or investigating a
repeated failure, and inspect no more than three candidates. Historical
conclusions do not support current claims without current artifacts.

## Hard boundaries

### Semantic authority

Take metrics, dimensions, Events, StateModels, participant roles,
relationships, units, definitions, and admissible joins from the semantic
catalog. Analysis may choose question-specific windows, policies, cohorts,
seeds, and completeness declarations, but must not add or edit semantic
definitions while this skill is active.

A missing or disputed reusable business object stops only the affected branch.
Record the smallest gap and hand it to `marivo-semantic`; the skill handoff does
not require user approval. Semantic authoring asks the user only when business
meaning remains unresolved. After readiness, resume the branch from the returned
`analysis_ready_inputs`. Do not silently substitute a physical column or a
different metric. The closed runtime-metric route above remains valid only for
question-scoped expressions over governed inputs.

### Typed execution

Do not query business rows through Ibis, DuckDB, pandas readers, backend
connections, or private datasource handles to bypass Marivo. Begin through the
registered public entry for the question family:

| Question family | Focused entry |
| --- | --- |
| Governed metric or runtime metric expression | `marivo.help("analysis.observe")` |
| Event subject journey | `marivo.help("analysis.events.match")` |
| Normative state replay | `marivo.help("analysis.lifecycle.replay")` |

After entry, follow the concrete artifact contract or focused live help. Let
those surfaces choose valid method, mode, and composition options; do not
emulate unsupported typed operations or strengthen an affordance into a
recommendation or causal claim.

### Terminal custom analysis

Use `frame.to_pandas()` only after a bounded typed artifact establishes the
semantic input, scope, and evidence chain, and only when the remaining
calculation is intentionally custom or unsupported by typed analysis. Do not
feed terminal results back into typed analysis.

Do not inventory installed packages or site-package directories. Attempt at
most one targeted import for a method you intend to use; if it is unavailable,
use a simpler method supported by the known runtime rather than probing for
alternative libraries.

Keep terminal calculations in the same rerunnable script as the
`frame.to_pandas()` call. Never export artifact rows to CSV, Parquet, JSON, or
another file and reload them with a generic reader. On rerun, reacquire the
typed artifact through the public session surface and convert it directly.

Use `md.raw_sql(...)` only when a semantic gap blocks typed work and project
policy permits it. State the missing object, purpose, temporary assumptions,
and loss of typed continuity. A delivery or publication request belongs to its
independent delivery capability.

### Evidence integrity

Keep material quantitative claims recoverable to current semantic inputs,
session/job, artifact, and scope. Scripts, chat summaries, historical sessions,
and artifact digests are navigation aids, not substitutes for current evidence.

Separate:

- observed facts returned or computed from current artifacts;
- interpretation supported by those facts;
- hypotheses or recommendations that require judgment;
- unsupported or blocked questions.

Preserve coverage, censoring, reconciliation, statistical uncertainty, and
quality limits. Never turn association into causation, absence into zero, a
point forecast into certainty, or a partial segment result into a population
claim.

When claiming a group difference or adjusted relationship, report the minimum
verification surface needed for that claim: population and sample size,
estimate, uncertainty or test evidence, and material model or distribution
diagnostics. If that evidence is unavailable, weaken the claim instead of
filling the report with additional unverified methods.

When reporting a fitted model, include its fit, coefficient uncertainty (a
standard error or interval), and at least one material diagnostic or robustness
check. A coefficient and p-value alone are not a sufficient verification
surface.

Match the strength and granularity of recommendations to the evidence. When
required operational inputs are absent, prefer conditional actions, monitoring,
or bounded tests over irreversible actions or invented numeric targets.

## Script discipline

Store ordered step scripts under
`<project_root>/.marivo/analysis/sessions/<session.id>/scripts/`. Once executed,
each script is an immutable source record for one decision round. Batch
compatible operations; do not split mechanically by operator.

Carry dependencies as exact artifact refs and restore them with
`session.get_frame(ref)`. Do not share Python variables, import prior scripts,
select an implicit "latest" result, or print restored inputs again. Label new
operations with a concise `analysis_purpose` and show only their new artifacts.

At closeout, use bounded frame summaries, recent jobs, evidence digests, and
findings before loading exact supporting artifacts. The agent owns synthesis;
do not dump the whole session or treat historical conclusions as current evidence.

## Closeout

When a material conclusion depends on a runtime metric expression, read the
[runtime metric closeout](references/runtime-metric-closeout.md) before writing
the answer.

Answer the requested questions first. Use compact tables only when they make a
comparison easier to verify. For every material conclusion:

- first state the literal direction and strength of the primary requested
  statistic, then add significance, nonlinearity, heterogeneity, or other nuance;
- give the key magnitude or uncertainty needed to understand it;
- distinguish evidence from interpretation and recommendation;
- disclose blockers, warnings, omissions, quality limits, and terminal exits;
- preserve requested source names or business terminology;
- keep internal evidence recoverable without turning runtime mechanics into
  report content.

Write in the user's business vocabulary. By default, do not expose Marivo,
governance, semantic-catalog, typed-object, operator, session, artifact, frame,
observation, or conversion terminology in the report. Translate validity notes
into source, scope, definition, and evidence language. Retain internal terms
only when the user explicitly requests a Marivo or implementation audit.
Before delivery, scan the draft once for those internal terms and rewrite every
occurrence; a data-and-scope or limitations section is not an exception.

State a key result once. Let the executive summary point to the supporting
table or section instead of repeating its full evidence, and let
recommendations cite the finding rather than restating it. Keep provenance to a
single plain-language source sentence when it helps interpret the evidence.
Do not duplicate a detail table in the summary and body; keep the summary to
answer-first prose or a non-repeated decision table.
Unless the user requests an audit or reproducibility handoff, omit recovery
notes, session and artifact identifiers, script paths, environment details, and
execution logs.

Check the deliverable against the original coverage checklist before stopping.
Do not keep exploring after all required answers are supported and limitations
are explicit. Once a complete deliverable exists, allow at most one read and
revision pass for material consistency gaps, then stop.

Use this answer-first presentation order when the user did not request another
format; adapt it to the question rather than treating it as a fixed template:

1. State the decision-relevant answers or findings in a short summary.
2. Organize compact evidence by the user's questions, not by Marivo operators.
3. Tie implications or recommendations back to those findings and their
   confidence.
4. End with material limitations; add provenance only when it changes how the
   evidence should be interpreted or the user requested it.

Keep setup and method to the few sentences needed to interpret the evidence.
Do not include the working checklist, environment fingerprint, script list,
content hashes, or full artifact inventory unless the user requests an audit.
When exact source names are requested, use them where the finding appears
instead of repeating a separate mapping catalog.

Record Marivo product friction privately under
`<project_root>/.marivo/analysis/internal_feedback/<session.id>.md`; disclose it
to the user only when it affects validity, coverage, confidence, or completion.
Do not expose absolute interpreter or package paths in user-facing outputs.
