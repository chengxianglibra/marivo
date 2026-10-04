# R8.6 local acceptance and R9/R10 handoff

Date: 2026-10-04. Status: **implemented with bounded evidence; R8.6 remains incomplete**.
The unchanged R8.1 denominator contains 492 R8.6 obligations: **287 passed and
205 unverified**. Earlier open phase requirements remain open. Successful scripts,
transport, package checks and aggregate test totals do not close other original IDs.

Entry was clean `panda@7ecf92daea9f13ee04b9ca4c046a191d5b3e680f`.
The [summary](2026-10-04-marivo-r86-evidence/summary.json) binds the entry owner
hashes, commands and attachment digests. The immutable snapshot SHA-256 remains
`b768dd67a0a3a354b2133c05ff323e0f0344f7f4d08ada372aa10f0b784349b5`.
Evidence was collected before committing this implementation. AGENTS.md, packaged skills, earlier
qualification ledgers and the historical snapshot are unchanged.

## Implementation and actual execution

The common graph publisher now enables the existing 600-second budget for R8
fit/condition/pair/training parts, including fixed results and views. Each execute
resets inherited committed state, so a prior successful publication cannot disable
later deadline checks. No input-row, byte or memory admission limit is added.

Runs recovery previously called `compute(capture)` and segmented the retained
input again. It now checks saved interval witnesses: schema, input rows, series
adjacency, maximality, order, identity, endpoints, count, Duration, termination
neighbors and complete true-cell coverage. Recovery can verify the original
condition classifications without calling the segmentation kernel. The explicit
zero-Findings policy is also checked; malformed policy payloads previously escaped
the runs-specific validator. Six independent witness mutations prove rejection
while segmentation is forbidden.

The shared installed-package probe now checks the four current Result families
and rejects the retired Candidate/Association/Forecast Dataset surface. The new
R8 wheel gate stages only its selected test inputs and helpers, outside the source
checkout, with package origin guarded at process start and exit.

`tests/r86_journeys.py` and `tests/r86_worker.py` execute all nine methods through
public DSL, graph, Runtime and Store 7. Table and local Parquet each use separate
producer, fixed-kernel and cold-kernel processes. Each fixed/cold method really
executes its numerical kernel once on independently retained inputs; subsequent
exact hits execute no kernel and add no Run. The database, models and source files
are removed after production. Current Semantic loading, datasource sessions,
DuckDB and Ibis DuckDB are forbidden in the two offline processes. Reading every
saved Result forbids estimator fitting/scoring/training and runs segmentation.

The frozen base profiles are int64, composite `(string,int64)` Entity identities,
and UTC/us complete builtin-day Time grids. Independent rational score, raw
identity/grid and normative correlation/forecast expectations check values,
contracts, Findings and primary/part receipt digests. The additional Parquet
profiles remain separate from the frozen table-only R8.6 IDs.

A11 source compositions execute current-vs-baseline scoring, state then numeric
selection, real selected members, next observation and `count_defined` in one
DAG. The trace verifies every source preparation precedes the single fit; the
selected observation contains the original member `c` with value 7. Category×Time
correlation uses three quantities `(int64,float64,decimal(38,6))`, all three
methods and lags, with selected coefficient rank/table. All three forecast models
use synchronized prediction/lower/upper tables. Separate Region, Channel and
joint attribution preparations preserve the raw-fact target. That attribution
regression does not attach deviation-method V16 IDs merely because it passes.
Supplementary table/Parquet checks execute signed direct runs, both score-runs
chains and all-unavailable gaps. Their explicit next-round attribution on an
empty future window rejects `key_set_equal` with three violating rows. These
global-Time counterexamples differ from the frozen Entity×Time/Subject-map
next-round profile; they remain separate blocking evidence and grant no original
ID. They do not redefine that mandatory composition as an accepted refusal.

## Original requirements and remaining exits

The [requirements ledger](2026-10-04-marivo-r86-evidence/requirements.json.gz)
retains every original row and exact method, implementation/version, precision,
input type/domain, key/time/origin profile, route, RequiredParts and process
proof class. A proof must match its original scenario and have a successful
assertion-owner command with the retained log digest. Installed qualification
also requires the candidate hash, isolated imports and successful gate receipts.

| Frozen family | Passed | Unverified | Evidence boundary |
| --- | ---: | ---: | --- |
| V16 public compositions | 8 | 178 | Exact source score-members, Category×Time correlations and forecasts; remaining full A11/A02/A04/A07/J1–J4 method/process profiles have no exact attachment |
| V17 process/reuse/sharing | 72 | 0 | Nine real source/fixed/cold methods, new source realizations, exact hit, shared DAG and transport separated from kernels |
| V18 corruption/resources | 171 | 9 | Independent parts, versions, keys, captured scope, receipts, Findings body/digest/order/cap/input; nine fault scenarios per method; dedicated empty-Findings obligations remain unattached |
| V20 installed package | 36 | 18 | Nine cold results, archive/dependencies, site-packages and poisoned source; complete method-specific installed A11/A04 obligations remain open |
| Total | 287 | 205 | R8.6 incomplete |

Receipt/part tests cover public read, recovery and fixed exact-hit rejection.
Source corruption changes each authority independently and restores transaction
state between cases. Fault tests preserve the previous artifact and verify
artifact/Evidence/Findings counts and an empty resource journal for cancellation,
malformed batches, close failure, precommit failure, lost durable commit
acknowledgement and late completion. Deadline boundaries use an injected monotonic
clock at 599.999, 600 and 600.001 seconds; these are deterministic local checks,
not remote cancellation or a real 600-second service run.

The [full R8 handoff](2026-10-04-marivo-r86-evidence/r8-handoff.json.gz) preserves
all **53,695 original IDs**, referencing the original owning ledgers. R8.2's
15,564-row current ledger omits 98 frozen IDs; this handoff retains those IDs as
unverified without reconstructing missing qualification. Original denominator
accounting is therefore:

| Owner | Passed | Blocked | Unverified | Original total |
| --- | ---: | ---: | ---: | ---: |
| R8.2 | 13,212 | 2,280 | 170 | 15,662 |
| R8.3 | 3 | 0 | 1,705 | 1,708 |
| R8.4 | 18 | 256 | 35,388 | 35,662 |
| R8.5 | 171 | 0 | 0 | 171 |
| R8.6 | 287 | 0 | 205 | 492 |

The predecessor ledger bytes and their narrower historical counts are unchanged.
Full R8 acceptance is not granted. No local script supplies remote, native-route,
real-Agent or release qualification.

## Verification and candidate authority

The candidate is `marivo-0.5.3.dev0`:

- Wheel SHA-256: `1b5a3f30e9c6aa33cacabfb7428fd0e6886b6e2f4efe295e06740d235fac1961`.
- Sdist SHA-256: `99169400a12fb5f57ccd8faef795d78035f565f453369c9d98fe3db3888090ca`.

Archive members match the current product Python/skill inventory exactly. The
isolated environment installs this noneditable wheel under pinned dependency
constraints, passes `pip check`, checks `direct_url.json` against the archive hash,
rejects actual source `PYTHONPATH` pollution, checks surface equality and records
all child import origins. Its initial gate passes 100 tests and six explicit
Spearman A04 table/Parquet producer/continue/recover phases. These A04 proofs have
their actual narrower keys; they do not qualify all nine method-specific V20 A04
rows. The appended fault and attribution regressions use the same archive in a
second isolated environment, with separate commands and input hashes.
The supplemental commands pass 106 fault/attribution cases and 11 sharing/runs
counterexamples; their original scopes and distinct input manifests are retained.

| Successful scope | Result |
| --- | --- |
| Public A11/process tests plus retained R8.3 Runtime | 13 passed |
| Nine-method resource and fixed receipt/part checks | 90 passed |
| Runs witness counterexamples | 6 passed |
| Source corruption authorities | 9 passed |
| Shared method and transport distinction | 9 passed |
| Separate-axis/joint attribution | 1 passed |
| Direct/score/unavailable runs and blocked next-round diagnostics | 2 counterexample tests passed; next-round acceptance remains open |
| Independent ledger, installed staging and runs-kernel default tests | 32 passed |
| Compact broad gate | 5,894 passed / 5 skipped; lint/imports, 412 source modules and API docs passed |
| Touched strict typing | 10 modules; dependency diagnostics excluded with `--follow-imports=silent`, no touched module excluded |
| Package build/check | `make pypi-build pypi-check` passed |
| API and latest EN/ZH site | API generation passed; 321 site pages and postbuild checks passed |

Overlapping tests and repeated attempts are not summed. All failed iteration logs
are retained. Initial faults exposed runs policy acceptance and the inherited
committed-state deadline leak; both were fixed and rerun. Other attempts corrected
test oracles and setup: sparse contribution grouping legitimately lacked complete
grids, coefficient Findings eligible count includes only defined candidates,
drift selection removes negative predictions, and Store 7 Finding inserts use
five columns. A direct order axis is required for contribution attribution;
separate-axis endpoint preparations avoid a pre-existing duplicate-coordinate
rejection when both coordinates are retained. The attempted source `row.sum`
int64/instant route remains unqualified; the accepted A11 continuation explicitly
uses `count_defined`. No R5/R6 qualification was added to bypass those refusals.

## Concrete R9 and R10 handoff

The [R9 target catalogue](2026-10-04-marivo-r86-evidence/r9-targets.json.gz)
contains 9,109 frozen profile seeds and the sole-owner physical-form expansion
for DuckDB, PostgreSQL, MySQL, SQLite, Trino and ClickHouse. It is a static required
target catalogue, not an execution ledger or method registry. Each method/version,
input-type/domain/key/time seed needs preselected Ibis preparation and fixed
routes, provider-specific forms, exact input/receipt/oracle checks, actual issued
Ibis-to-driver statements, submit counts, observed cost and cancellation/cleanup.
Trino Iceberg/non-Iceberg and ClickHouse MergeTree/Distributed remain distinct.
Native attempts do not replace mandatory preparation routes. The [R8.5 index](2026-10-04-marivo-r85-evidence-index.md)
owns M01–M18 SQL/helper retirement; the [SQL ledger](2026-09-26-marivo-full-refactor-r0-sql-ledger.md#4-六后端目标资格矩阵)
owns provider-specific extensions and remaining DS/AN duties.

R10 receives the same candidate hash, installed public scripts, `marivo.help()`
then `marivo.help("analysis")`, focused current Help and actual Result contracts.
To reproduce local cold boundaries, run the worker's `produce`, `fixed`, then
`cold` modes in separate processes on one project; its offline guards and raw
expectations are part of the script. The installed command manifests contain the
exact interpreter/cwd/environment authority, and structured refusals retain their
own repair/Help targets. Remaining A11 obligations and predecessor blocked keys
must be resolved before complete local acceptance. A real Agent must independently
choose metrics, thresholds, axes, business questions and continuations; these
scripted tests provide no real-Agent evidence. R9/R10 work was not started.

## Retention and reproduction

Ignored raw files live under `docs/superpowers/specs/evidence/r86/`. Durable
compressed attachments and a hash index accompany this document; compressed and
raw digests remain distinct. Restore raw attachments from the deterministic gzip
shards using the [attachment index](2026-10-04-marivo-r86-evidence/attachments-index.json).
The reader checks each shard, the reconstructed gzip and every raw file hash:
The [final verification](2026-10-04-marivo-r86-evidence/verification.json) records
a successful three-shard/385-file restoration and byte-identical regeneration of
the original-ID ledgers and target catalogue. The
[source identity](2026-10-04-marivo-r86-evidence/source-identity.json.gz) records
entry/current hashes for every changed code, test, script and document.

```sh
.venv/bin/python -m scripts.r86_acceptance_requirements \
  docs/superpowers/specs/evidence/r86 \
  docs/superpowers/specs/2026-10-04-marivo-r86-evidence \
  --restore docs/superpowers/specs/2026-10-04-marivo-r86-evidence/attachments-index.json
```

The candidate archives remain local under `dist/pypi/`; the archive member inventory
and both hashes are retained. Rebuilding an archive produces a new candidate hash
and requires its own installed gate. At evidence collection time, no commit,
push, release-check, MinIO, external service, real-Agent run or publication occurred.
