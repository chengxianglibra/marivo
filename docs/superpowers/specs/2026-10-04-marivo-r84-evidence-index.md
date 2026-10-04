# R8.4 association and forecast implementation evidence

Status: **connected implementation with bounded evidence; R8.4 is not fully accepted**.

Entry branch was `panda`, HEAD `61915fd5405a5811056fb7f243decc9ecc67478d`.
The entry index, unstaged diff and nonignored untracked list were empty. This task
does not commit, push, edit AGENTS.md or edit packaged skills. R8.2 remains 15,564
mandatory IDs with 13,212 passed, 2,280 blocked and 72 unverified. R8.3 remains
1,708 mandatory IDs with 3 passed and 1,705 unverified. Their acceptance is unchanged.
R8.5 retirement, R8.6 same-wheel acceptance, R9 and R10 retain their own duties.

## Connected behavior

The frozen concrete Numeric receivers construct Logical association and forecast
graphs, including fixed-only receivers. Association accepts 2..16 distinct ordered
quantities and evaluates every unordered pair for Pearson, Spearman and Kendall
tau-b. Default Pearson is a breaking change; predecessor J4/A04 calls explicitly
request Spearman. Coefficient and selected are owned views. Result selection,
coefficient ranking and same-scope tables retain all original candidates, counts,
status and winning-lag authority. Positive lag pairs left(t) with right(t+k) on
captured complete grid ordinals. Only ordinary Null pairs are deleted.

Forecast uses the existing sealed horizon and model factories. All three models
publish synchronized prediction/lower/upper views on an approved captured future
grid. Their per-series training state retains innovations, degrees of freedom,
slope, unrounded variance, exact-zero status and each horizon variance. Bound sum
rejects at construction. Original I/F/D carriers are captured without float
coercion; rational arithmetic precedes the final coefficient or unit-valued
rounding. Unitized Decimal output uses Decimal(38,max(s,6)). Root and inverse-normal
certificates use isolated directed Decimal enclosures and precision refinement.

The existing typed graph, sole MethodRegistry, source preparation, Runtime and
Store 7 own execution. New statistical consumers preselect exact local routes
(`ibis_python` for source preparation, `artifact_python` for retained inputs).
They do not retry a failed native route. No new native statistical contract has
been certified; the frozen 256 native requirements remain blocked. Existing R4
fixed Spearman tests remain predecessor evidence, separate from R8 qualification.

Store parts include pair_inputs/association_state or
training_inputs/forecast_state/future_cells, plus original complete grid_cells
and finding_policy when temporal. Recovery validates receipts, digests, exact
keys, counts and numerical witnesses using captured facts. It does not call an
estimator, solve ranks, select a new lag, regenerate predictions, load current
Semantic/calendar state or reconnect a datasource. Selection retains the full
original scope. Closed V2 graph quantity subjects use the common Findings codec,
cap 1000, prescribed order and Artifact/definition rebinding. Main table, parts,
Evidence, Findings and terminal Run use the existing atomic transaction.

## Immutable requirement denominator

`scripts/r84_statistics_requirements.py` extracts all **35,662** frozen R8.4 IDs.
The historical R8.1 snapshot is unchanged. The
[requirement ledger](2026-10-04-marivo-r84-evidence/requirements.json.gz) preserves
every original field and ID. Only exact matches to the
[three-process manifest](2026-10-04-marivo-r84-evidence/public-processes.json) gain
passed status: ordered method/type/domain/route/origin keys, implementation and
contract versions, numerical policy, original state codecs, required parts and
source-offline authority must match together.

| Mandatory status | Count |
| --- | ---: |
| passed | 18 |
| failed | 0 |
| blocked | 256 |
| skipped | 0 |
| unverified | 35,388 |

The 18 attached keys are the three correlation methods over ordered int64/float64
complete time inputs and the three forecast models over int64 complete time
inputs, each in source, fixed and fresh-process source-offline execution. The
fixture origin is Parquet microseconds, UTC builtin days with string time-cell
keys. Restored exact hits and selected views are additional checks, not substitute
fixed-kernel qualification. Arity, Calendar, Decimal, fault, composition and
disclosure checks below do not automatically close another frozen ID or scenario.
The full mandatory matrix remains open, so this is not phase acceptance.

## Verification boundary

The independent kernel tests calculate centered rational sums, original order and
ties, paired counts and the normative innovation/variance equations. A separately
authored 220-digit Decimal normal integral checks root/quantile enclosures.
Public Runtime tests cover source/fixed methods, logical projections, selected
scope, coefficient rank/table, three-model view tables, nonempty Findings reads,
2/3/16 quantities, signed lag counts, selection ties and approved unequal calendar
continuation. Fault tests remove/corrupt receipts and retained parts, change
Finding bodies/identity/digest, inject source-close and publication failures, and
check prior Artifacts and resource cleanup. Cap tests distinguish 1004 eligible
future points from 1000 emitted Findings and transport the original capped set
through empty output selection. A11 checks use one Logical execute for selected
correlation rank/table and synchronized forecast view tables.

Command results and final attachment hashes are recorded at the final checkpoint.
Default tests exclude Runtime-marked tests. No release gate, MinIO, wheel or real
Agent acceptance is implied by these source-tree checks. Earlier failed iterations
are repaired and rerun; they are not promoted to positive requirement evidence.

## Final checkpoint

The [entry baseline](2026-10-04-marivo-r84-evidence/entry-baseline.json) preserves
the observed entry state. The
[verification record](2026-10-04-marivo-r84-evidence/verification.json) attaches
command results, retained logs and their SHA-256 digests. Commands omit temporary
log redirection; the historical failed iteration is identified by its tested
scope rather than reconstructing an unavailable command string.

| Check | Result | Evidence |
| --- | --- | --- |
| `make check-agent` | Exit 0; 6,090 passed, 5 skipped; lint, import contracts, 439 typed modules and API docs passed | [Log](2026-10-04-marivo-r84-evidence/logs/check-done.txt) |
| Touched test/evidence typing | Exit 0; six modules | [Log](2026-10-04-marivo-r84-evidence/logs/newtypes-done.txt) |
| Independent kernel and exact-ID matcher | Exit 0; 55 passed | [Log](2026-10-04-marivo-r84-evidence/logs/oracle-final.txt) |
| Final focused Runtime | Exit 0; 20 passed, one three-process test deselected and executed separately; one shared fixture deprecation warning | [Log](2026-10-04-marivo-r84-evidence/logs/focused-checkpoint.txt) |
| A11 and Findings cap composition | Exit 0; two passed | [Log](2026-10-04-marivo-r84-evidence/logs/compositions3.txt) |
| Source/fixed/cold statistical kernels | Exit 0; one three-process test, 18 exact proof keys | [Log](2026-10-04-marivo-r84-evidence/logs/proof-done.txt), [Manifest](2026-10-04-marivo-r84-evidence/public-processes.json) |
| J4 producer/continuation/recovery | Exit 0; six independent source-tree processes across table and Parquet | [Log](2026-10-04-marivo-r84-evidence/logs/j4-processes-final.txt), [Manifest](2026-10-04-marivo-r84-evidence/j4-processes.json) |
| Site build | Exit 0; Astro check, 321 pages, bilingual search and install-script verification | [Log](2026-10-04-marivo-r84-evidence/logs/site.txt.gz) |
| Final exact-ID matcher | Exit 0; 16 passed, including complete frozen-field and oracle preservation | [Log](2026-10-04-marivo-r84-evidence/logs/ledger-check.txt) |

The cap diagnostic rerun passed. Its 20-second faulthandler timer emitted an
ordinary validation stack; it was not a Runtime deadline failure. The earlier
combined Runtime run had one stale J4 view-column assertion. That assertion was
updated to the canonical `value` column and its targeted rerun passed. Neither
the diagnostic stack nor repaired iteration changes mandatory ID accounting.

| Attachment | SHA-256 |
| --- | --- |
| Entry baseline | `d8788810f954120ed34fa1fed316e7ff88852a43556aa27b8cbb77e9de1c7592` |
| Statistical process manifest | `e636331c2c88945ad57a47b11986f0fa918a05417b26be0d4149e22c97daa199` |
| Full R8.4 requirement ledger | `82b5da3c81212d2e4e7b9e360f7e65aeddd7a9ca50d20dcd07f67186e64452d5` |
| Source-tree J4 manifest | `17547cc05e07f591570ce57f5873cafe6e85d9928207fa09a606206dca4372a5` |

The gzip ledger deterministically reconstructs all 35,662 original IDs, retaining
ordered type/route keys, actual method contracts, oracle descriptions, successful
commands and matching attachment digests. It is below the repository's 1,000 KiB
added-file limit. Required cells remain open; R8.4 remains incomplete.

## Adversarial review repair checkpoint

The reviewed `panda` snapshot used HEAD
`61915fd5405a5811056fb7f243decc9ecc67478d`, an empty staged set and 77 changed
files with content digest
`d27bd489907208e69b048b1d96f6a18041515c719de5a0f1a26be403ab4435a6`.
The [repair record](2026-10-04-marivo-r84-evidence/review-fixes.json) attaches
commands, exit codes, log digests and tested implementation/test hashes.

All three reproduced findings are fixed: statistical rank projections retain
original capture/state and Findings, same-scope rank tables verify captured
columns through `table_fits`, builtin future lookahead does not reject unused
DST gaps/folds, and Spearman frozen witnesses use an O(n log n) ordered tie-block
verification instead of a quadratic all-pairs scan. Recovery does not call the
rank estimator. Source and fixed rank/table tests include disconnected sources,
saved-table recovery, preserved Finding counts and malformed-part rejection.
Six civil boundary cases include both hourly and minute grids. The 256-value
rank witness check bounds comparisons and rejects incomplete or altered ranks.

| Repair validation | Result |
| --- | --- |
| Narrow kernel, evidence and new default regressions | 63 passed |
| New regression typing and repository lint | Passed |
| `make check-agent` | 6,097 passed, 5 skipped; 439 typed modules; lint, imports and API docs passed |
| Statistical focused Runtime, including three-process recovery, A11 and cap | 25 passed; one existing fixture deprecation warning |
| Shared deviation/display and statistical rank regression Runtime | 28 passed |

The two Runtime scopes overlap on the four new statistical rank tests; their
counts are not a distinct-test total. The original Runtime and site logs are preserved byte-for-byte as gzip files,
with both compressed and decompressed hashes recorded. The requirement ledger
bytes remain unchanged: 18 passed, 256 blocked and 35,388 unverified out of 35,662, with no
failed or skipped IDs. R8.4 remains incomplete. No release gate, wheel acceptance,
commit, push, AGENTS.md edit or packaged-skill edit was performed.
