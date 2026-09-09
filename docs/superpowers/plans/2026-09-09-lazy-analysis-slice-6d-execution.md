# Slice 6d: private Entity-outlier Candidate membership

Status: complete. This record closes private Slice 6d only.

## Baseline and scope

The owner approved the complete Slice 6d implementation plan and selected the
unscaled mean absolute-deviation fallback. Work starts on clean `lazy-dataset`
HEAD `e146a279591763c87703290fc1bc6cb70f87275e`, containing the committed Slice
6c implementation and follow-up. The earlier planning baseline was independently
matched to Slice 6c's recorded executable digest.

This slice adds private Entity-outlier production, Candidate row continuations,
and explicit Metric population input. Public exports, Help, current site docs,
Event/Lifecycle integration, driver-axis discovery and release work retain their
own later slices. No commit or push is part of this task.

## Implementation ownership

- Candidate contracts own the exact Entity shape, source-free construction,
  generated selectors, paired registration and the closed Entity evaluation
  summary. Existing time Candidate behavior remains separately registered.
- Compiler owns the native DuckDB scorer, versioned typed item digest, frozen
  source input, aggregate-only validation/proof and same-domain engine scans.
- Observation owns exact Candidate population admission and independent
  selection/observation scopes. Publication owns membership provenance and
  removes Candidate-only Evidence from the consuming Metric descriptor.
- Runtime owns source-proof consumption, identity-safe atomic publication,
  closed codecs, cold validation, and bounded explicit terminal reads.

The integration owner coordinates shared seams. Parallel workers preserve one
another's changes. Reusable fixtures use isolated project roots and connections;
ordinary default checks and focused Runtime gates stay separate.

## Frozen behavior

Only one quantitative `metric/entity@v1` input can invoke
`discover.entity_outliers(threshold=3.0, limit=50)`. Candidate columns and the
Entity-only key follow the owning typed-operator design. Positive finite
thresholds and integer limits in [1, 1000] retain the common validation rules.

The source method uses median center, scaled MAD, then unscaled mean absolute
deviation around that same median. Nulls are excluded; insufficient/constant or
non-finite/overflow input fails. Threshold equality qualifies. The fixed reason
is `entity_mad_threshold_met`; scale methods are `mad` and
`mean_absolute_deviation`. The owning design records the complete amendment.

Logical and compatible engine-retained Entity Candidates may supply exact
selected identities to Metric `population=`. No detached membership value,
Python collection bridge, or origin replay is introduced. Identity-preserving
source work has no local retry. Authorized local/object output remains terminal
storage; its identity cannot be uploaded to regain source capability.

## Acceptance matrix

- Pure contract/input/schema/selector and private registration tests.
- Independent MAD/fallback numerical references, boundary values, complete
  identity/digest uniqueness and typed source encoding vectors.
- Logical and engine-checkpoint production and membership, independent windows,
  rejected Candidate variants and foreign ownership, empty results and sampling.
- Adversarial no-local-identity checks, source/cancellation/publication failures,
  corrupted metadata, and authorized storage/terminal-read checks.
- A three-process producer, source-offline membership continuation and exact
  cold-binding journey with unchanged authoritative rows, Evidence and Runs.
- Affected time Candidate, retained membership and engine adapter regressions;
  focused typing, `make check-agent`, and focused `make runtime-test` on one
  unchanged executable candidate. Final evidence and status are recorded below
  only after those gates complete.

## Implementation and review disposition

The Entity-specific source method and its row successors have no pandas
implementation. A source-private `CompiledRelationFence` freezes each scorer's
Metric input once; its validations, output and scalar summary use that same
realization. Native identity encodings preserve the registered ordered signature
and normalize floating signed zero. Pre-limit validation covers complete input
identity and item-id uniqueness, including identities whose observations are null.
Numeric conversion checks reject lossy integer/decimal round-trips.

The Runtime consumes only a single bounded scalar proof record, checks current
Candidate rows natively against the original center/scale and item definition,
and publishes zero Findings without a generic Candidate row scan. Authorized
storage streaming and explicit terminal reads remain separately guarded. The
descriptor requires the native output validation and exact agreement between
its governed identity field and Population authority.

Metric membership uses only retained identities and the required predicate
fields, with immutable receipt and native identity validation. It does not
rescan unused Candidate score columns to repeat discovery. Downstream Metric
publication removes Candidate-only Evidence while retaining the selected
definition, independent time scope and original sampling receipts.

Independent review traced source realization, scoring, pre-limit identity and
digest checks, proof decoding, cold authority, and identity-safe publication.
The discovered cold-read signature mismatch is fixed and regression-covered.
Time Candidate tests now explicitly narrow their evaluation variant. The
sampling regression reads the existing Metric `show()` disclosure, rather than
assuming that Metric contracts expose Candidate-specific approximation fields.
No additional actionable review finding remained at closeout.

## Initial acceptance candidate and gates

The initially accepted 886-file executable candidate is
`69835ea9397f8a4f07b8200440f76f0370ef63cfd755908c3431f0a880c9275b`.
The manifest hashes every Python file under `marivo/` and `tests/`, plus
`pyproject.toml`, `Makefile` and `.importlinter`, in lexical path order, using
UTF-8 path, NUL, file bytes, NUL. Documentation and generated evidence are excluded.

`evidence/slice-6d/final/` records these passing gates with identical before/after
candidate fingerprints:

| Gate | Evidence |
| --- | --- |
| Focused contracts, compiler, independent numerical references and publication tests | 196 passed; pytest time 12.08 seconds, gate wall time 13.089 seconds |
| Strict typing for new and affected Candidate test/fixture modules | 18 source files passed |
| `make check-agent` | 6,625 default tests passed in 105.19 seconds; full lint/import contracts, production typing and API documentation passed |
| Focused Runtime and affected regressions | 117 passed in 202.85 seconds with two workers |

Durations alongside test counts are reported by pytest. Gate JSON records the
complete command's wall time, including startup and wrapper overhead; these
measurements have different boundaries.

The Runtime gate includes logical Entity membership, independent observation
windows, valid empty selection, sampling inheritance, foreign-domain/local/object
rejection before Run creation, source failure without local retry, cancellation,
atomic publication rollback, cold corruption, and a real engine Metric
checkpoint scored after its source is removed. Existing time Candidate,
retained membership, retained Metric and engine adapter tests remain covered.

The independent three-process journey proves logical Candidate membership,
native Candidate production, source removal, recovered Candidate filtering and
ranking, downstream Metric observation, and exact cold binding. It compares
immutable Artifact/Evidence authority, scores and selected totals without
putting raw identities in the evidence record. The final cold phase adds no
Run, Artifact or query. Native SDK stubs and real Store files cover successful
version-pinned object publication and explicit terminal reads; no object service
was started.

The terminal record binds the base HEAD, exact candidate, complete changed-file
inventory, patch and recovery evidence. `git diff --check` passed. No public
export, Help entry, site documentation, persistence generation, commit, push,
release or full release Runtime gate was changed or performed.

## Supplied review follow-up (2026-09-09)

The four Minor suggestions were checked against the implementation and original
gate output. Two bounded clarifications were adopted:

- The focused gate now labels both the pytest duration (12.08 seconds) and the
  complete command wall time (13.089 seconds). Both original measurements were
  correct; neither evidence value was replaced.
- The membership projection now explains that it uses the scored relation and
  reuses its frozen Metric input. This does not claim that every scoring
  expression is evaluated only once.

The additional compiler guard was not adopted: `lower_entity_candidate` returns
a non-optional proof, followed immediately by the non-optional definition
binding. The Runtime guard protects optional fields on the generic compiled
result and remains at that boundary. The timezone suggestion concerns a future
identity contract; this follow-up leaves the current exact type checks and
Slice 7 scope unchanged.

The comment changes the 886-file byte fingerprint to
`8cd7bc7cd27adc001e4c38d27d73e32f1a5038a553d7819b73fc8c2e3346db74`.
`evidence/slice-6d/review-followup/` records the before/after manifests, original
file hashes, follow-up patch, and validation. Only `lowering.py` changed among
manifest inputs, and its Python AST is identical with source locations excluded.
Focused `make lint-agent` and `git diff --check` passed. The original full gates
and terminal evidence remain unchanged and bound to the initial fingerprint;
they were not rerun or relabeled for this comment-only source change.
