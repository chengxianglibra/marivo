# Slice 8a isolated atomic cutover assembly

## Delivery boundary

The candidate is assembled in an isolated checkout. The working project remains
on its existing public surface. The delivery is a complete patch, frozen baseline
and candidate fingerprints, interface mapping, deletion and test replacement
inventory, validation records and Slice 8b application instructions. The owner
subsequently authorized a local commit of this isolated candidate using the
commit-attribution skill. Push, installation, release and application to the
source checkout remain outside this task. Slice 8c and Slice 9 retain their
separate acceptance ownership.

Final baseline HEAD after the accepted 3a temporal supplement:
`407c8c35eb4a3364797f8540f8ddfc123c291d08`. The preceding 3b baseline was
`5ae1de7eac4d8a610eda764b8641904894b09603`. The original `a813d2f8` baseline
and candidate checkpoint are preserved in `before-3b/` in the assembly directory.
The source checkout has an existing untracked `evidence/` directory. Its tracked
content and HEAD are recorded in the assembly's `baseline.json`; previous
Lifecycle work is part of the frozen source content. The existing Slice 5b
private acceptance and native disclosure prerequisite evidence are reused.

## Owner decisions during assembly

The owner selected project configuration in `marivo.toml`, then explicitly
removed database storage of `execute()` results. With no storage setting,
results use project-local Parquet. Only an explicitly selected object store
changes that destination. There is no size-based target selection, target retry,
executor retry or failure fallback.

The owner required retaining distinct and distribution capabilities and
completing their Parquet retention/recovery paths in their original Runtime and
operator scope before closing Slice 8a. The owner subsequently **fully removed**
the prohibition on native analysis of retained Parquet. This supersedes the
older no-Parquet-import/no-separate-native-analysis-domain clauses. Existing
registered algorithms may consume immutable local/object Parquet through a fixed
native query adapter selected before data work. Native query connections and
temporary relations are execution resources; they are never database-stored
Artifacts. Native execution still has its existing deadline, memory, cancellation
and ownership bounds. pandas methods retain complete-input admission.

Both percentile contracts remain supported, including the exact DuckDB T-Digest
implementation identity. No approximation replacement or algorithm fallback is
introduced to work around the removed database storage route.

## Implementation ownership and order

1. Freeze and inspect the prerequisite evidence and refresh Slice 0 inventories.
2. Assemble the exact 100 exports, concrete Session source/read bindings, semantic
   identity/versioning activation and native Help providers.
3. In the original Runtime/operator scope, replace database Artifact storage
   with independent Parquet parts and fixed native Parquet scans. Preserve
   membership/frequency schemas, independent cardinalities, private-state
   integrity checks, exact algorithms, source-offline recovery and atomic cleanup.
4. Bind project storage configuration. Resolve the selected target after Run
   admission; resolve credentials only for a selected object binding. Keep
   credentials in environment references and private runtime access only.
5. Complete deletion and test replacement, current specifications, EN/ZH latest
   documentation, runnable examples and packaged workflow skills.
6. Run interface, Help, protocol, semantic, skill/example and negative gates,
   targeted Runtime tests and `make check-agent`. Apply the full patch to a
   second copy of the identical baseline and compare candidate content.

## Project storage contract

No configuration is needed for project-local Parquet. To select object storage:

```toml
[analysis]
storage = "object:archive"

[analysis.object_stores.archive]
endpoint_url = "https://objects.example.test"
bucket = "analysis"
access_key_id_env = "MARIVO_ARCHIVE_ACCESS_ID"
secret_access_key_env = "MARIVO_ARCHIVE_SECRET"
# region = "us-east-1"
# session_token_env = "MARIVO_ARCHIVE_TOKEN"
```

`storage = "local"` explicitly selects the default. Named object bindings may
remain configured for reading existing Artifacts after the selected write target
changes. A missing binding, invalid field, absent environment value or unsupported
storage kind fails explicitly. The SDK does not search another credential source.
Changing the write setting does not relocate or rewrite an existing exact binding.

## Historical validation checkpoints

The following entries preserve intermediate failures and repairs. The final
acceptance section below supersedes their incomplete status.

- The initial assembled native registry renders 214 Help pages within the
  existing budgets; the facade exposes the accepted 100 exports.
- The first ten focused Runtime cases passed (52.47 seconds): distinct and both
  percentile methods, operand/Delta checkpoints, source-offline native
  continuation, independent private Parquet roles, and missing/mutated-state
  rejection with readable primary rows and no source replay.
- Final evidence must bind to the final candidate fingerprint. Intermediate
  test results do not close the broad gate or certify a deliverable patch.
- The first targeted typecheck exposed eager modules still scheduled for
  deletion; the atomic candidate must close these errors through the actual
  removal/replacement work before the final broad check.

## 2026-09-11 checkpoint after the Parquet amendment

The latest isolated implementation passed:

- 1,933 default Dataset tests after migrating private-surface assertions to the
  accepted public bindings;
- full source typing for 331 modules;
- 16 packaged skill checks;
- 22 targeted Runtime tests: exact distinct and both percentile methods,
  operand/Delta Parquet checkpoints, SDK-stubbed versioned object Parquet,
  source-offline cold continuation, missing/mutated private state, target
  configuration failure without fallback, public Session recovery, and the
  three independent-process distinct/distribution acceptance journeys.

Additional focused default evidence covers 43 public Session/timezone/Store
cases, 116 receipt/private-state/Event/Attribution cases, and 136 native
Help/export/no-I/O cases. These overlap the complete Dataset default run and
must not be added to it as a disjoint test count.

The source deletion now removes eager implementation chains, obsolete policy
constructors, database Artifact writers/readers, legacy exception symbols and
the eager constraint catalog. The canonical native ordering helper was retained
under the compiler and exercised through native Parquet scans. Existing empty
v0 Stores are rejected; fresh v3 initialization publishes only a complete closed
database, preserving concurrent creation without an in-place generation upgrade.

`make check-agent` was run and **failed** at default test collection because
remaining legacy suites import deleted eager modules. Lint/import contracts and
source typing passed before that failure. API documentation was not reached.
The latest English/Chinese documentation, complete legacy test replacement and
second-copy patch-application proof are still outstanding. No complete cutover
patch or Slice 8a completion is certified by this checkpoint.

At the preceding checkpoint, a required private capability gap was reproduced: the public/native input
contract promises Runtime Metric expressions, but Observation rejects them
unconditionally. See [the Slice 3b handoff](2026-09-11-lazy-analysis-slice-3b-runtime-metric-gap.md).
That prerequisite is now resolved by the 3b integration recorded below. The Parquet amendment
is not an excuse to narrow that independent capability or replace it with Help
text. The isolated candidate and its checkpoint fingerprints remain available;
the original checkout has not received the public switch.


## 2026-09-11 accepted 3b integration checkpoint

The complete Runtime Metric supplement from `5ae1de7e` is integrated into the
isolated candidate. Four overlapping files merged without conflicts, preserving
Parquet storage and the public facade. The original checkout remains at that
HEAD with only its pre-existing untracked `evidence/` directory.

The private acceptance manifest was verified against every source file rather
than inferred from a commit message. The isolated integration passed:

- 1,953 Dataset default tests, including native Help examples, independent
  reachability/budgets, ordered exports, protocols and source-free construction;
- 70 distinct targeted Runtime cases for mixed Runtime Metric forests, public
  observation and cold field projection, retained folds, both percentile methods,
  membership/distribution Parquet state, corruption, publication failure and
  source-offline/cold recovery;
- full typing of 331 source modules, formatting/lint/import contracts;
- 26 packaged-skill and Session-timezone checks.

`make check-agent` passed lint and typing, then stopped at five eager test import
errors. A separate complete collection audit lists 145 affected modules. They
remain test-migration work and are not ignored, skipped or removed to obtain a
passing gate. The API documentation stage was not reached.

The latest logs, interface mapping and checkpoint patch belong to the assembly
directory. Checkpoint replay proves only that the recorded candidate can be
reconstructed from this baseline. It does not approve that candidate for 8b or
certify 8a completion. The remaining work includes the full business/adversarial
test replacement map, public percentile method declaration, semantic/calendar
activation audit, current specifications and EN/ZH latest examples/disclosure,
and the final passing broad gate followed by final patch replay.

## Continued acceptance: API documentation, Decimal and temporal prerequisite

The current API reference now documents the exact ordered 100 analysis exports,
with actual Dataset methods and Session/runtime namespaces. Sphinx with warnings
as errors passes. Semantic quantile method selection has native Help for the
constructor, value and its render/show methods, explicit exact/approximate
meaning, EN/ZH disclosure, and a workflow decision in the packaged skill. The
analysis export count remains 100. Its semantic exports are also pinned in both
public-surface and semantic-import snapshots.

The latest focused export/Help/skill suite passes 172 tests. Exact Decimal
observation/schema agreement and Float/Decimal RuntimeMetric ratio pass two
real Runtime tests with local Parquet. The cumulative Decimal suite previously
passed five Runtime tests. Test replacement records retain the original
independent values; implicit Decimal-to-float coercion belongs to the removed
Frame contract and is not retained.

The full default diagnostic reached 4,809 passing tests, one now-repaired
semantic export snapshot failure, and 18 collection/setup errors from old
interfaces. That diagnostic explicitly continued past collection errors and is
not a successful broad gate. One old Decimal module has since been migrated;
the remaining legacy temporal and related suites still require complete
assertion migration. No collection exclusion or skip was added.

A missing private temporal capability is now reproduced on real source data and
assigned to [Slice 3a](2026-09-11-lazy-analysis-slice-3a-temporal-gap.md). Explicit
read timezone, native timestamp precision and string temporal parsing fail in
the unchanged private compiler; report timezone is not yet an input to that
contract. Three positive Runtime assertions remain failing. The accepted 3b
RuntimeMetric evidence does not certify those unimplemented temporal paths.

Slice 8a remains incomplete. Existing checkpoint patches are progress artifacts;
only a replay proof with the same current content fingerprint identifies a
reviewable checkpoint. Private temporal completion, complete test replacement,
remaining current EN/ZH disclosure, a passing full gate and final frozen patch
verification are required before marking Slice 8a complete.

## Final isolated assembly acceptance (2026-09-12)

Status: **Slice 8a complete in the isolated candidate**. Slice 8b has not been
applied to the source checkout. Slices 8c and 9 retain their own gates.

The complete private 3a supplement is integrated while retaining both calendar
snapshots and report-time authority. The prior 3b, 5b and native disclosure
acceptances remain accepted; they are not rerun as missing prerequisites.
The Slice 0 inventory is refreshed against the final baseline: all 100 ordered
analysis exports bind by object identity to native implementations, with their
focused Help routes and callable signatures. Semantic quantile method inputs
are explicitly exported and independently pinned; analysis remains at 100.

The final candidate passes `make check-agent`: 4,824 default tests, typing of
332 source modules, formatting, lint, import contracts and API documentation.
The final affected Runtime selection passes all 46 cases (99.37 seconds).
The obsolete local-worker assertion was repaired before this final run.
Source-offline exact values, zero source fences and the fixed native Parquet
query are asserted. Nine temporal integration cases and three executable
public documentation/native Help example cases also pass; these overlapping
sets are recorded separately rather than summed as disjoint coverage.
The EN/ZH site builds 321 pages and the required-content check passes.
The semantic tutorial now uses Dataset receiver methods, with all 45 Python
examples identical across EN/ZH and all 348 statically resolvable public
example calls matching current symbols/signatures. A real monthly observation
example verifies construction without datasource I/O and exact executed values.
Current project-configuration and telemetry pages reflect the fixed target and
pure-construction contracts. Old alignment APIs and Frame metadata are absent.

The delivery directory is
`/Users/lichengxiang/.codex/marivo-slice-8a-pikgczem/`:

- `slice-8a.patch`: complete atomic switch, including deletions;
- `baseline.json` and `candidate-final.json`: exact source and candidate content;
- `interface-mapping.json`: ordered exports, actual implementations and signatures;
- `deletion-and-test-replacement.json`: every removed file and all 123 retired
  test modules mapped to their native owners or explicitly removed contracts;
- `validation-final.json` and `forbidden-path-audit.json`: gate logs, hashes,
  current-code/disclosure scan scope and precise exceptions;
- `replay-proof.json`: clean second-copy application and byte equality;
- `ACCEPTANCE-FINAL.md`: final fingerprints, evidence and Slice 8b application steps.

The replay starts with a second copy of the same frozen source baseline, runs
`git apply --check`, applies the patch, compares every resulting file with the
candidate, and imports its own package to verify the 100 bindings. Main HEAD,
tracked content and the originally fingerprinted evidence remain unchanged.
No installed package or final release/Agent acceptance is claimed here.
