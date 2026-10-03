# R8.1 documentation and static freeze evidence

Date: 2026-10-03. Status: **R8.1 documentation/static freeze complete**.
Final validation completed 2026-10-04 Asia/Shanghai; artifact names retain their
2026-10-03 plan identity.

This index owns evidence accounting, not numerical or API rules. Sole F01-F14
owners, M01-M18 duties and future V01-V20/journey targets are indexed in the
[migration ledger](2026-10-03-marivo-full-algebra-dsl-r8-migration-ledger.md).
The [consumer snapshot](2026-10-03-marivo-r81-consumer-snapshot.json) is a closed
static capture. No new statistical method executes and no Runtime/backend/wheel
or real-Agent qualification is granted.

## Baseline and preserved state

Entry: panda at `4ffbc66ba2a718a083d7284fd40a40ff74fd80dc`. Entry status had six
staged user-owned qualification-untracking deletions, no unstaged diff and no
nonignored new files. Before edits, raw status/index/diffs/untracked outputs and
fourteen owner inputs were captured under ignored `.superpowers/r81/`; their
byte lengths, SHA-256 values, protected on-disk hashes and entry identity are
embedded in snapshot.baseline. Historical copies are inspection inputs, not
qualification reconstructed from transcripts.

The independent user-owned commit
`b779dcf31dbbfe3fe5ad08cc562bb0ee5bbf05f3` completed those exact six untracking
operations during this work. Entry HEAD and capture HEAD are separately bound.
No product, old test flow, AGENTS.md or packaged skill is changed by R8.1.
At the R8.1 capture point, no R8.1 file was staged; the scoped changes were
uncommitted.

## Reproducible capture and static limits

Run from the repository root with the repository virtual environment:

```sh
.venv/bin/python scripts/r81_static_freeze.py --baseline .superpowers/r81/baseline.json
.venv/bin/python scripts/r81_static_freeze.py --verify-current
.venv/bin/python scripts/r81_static_freeze.py --baseline .superpowers/r81/baseline.json --output .superpowers/r81/reproduced.json
cmp docs/superpowers/specs/2026-10-03-marivo-r81-consumer-snapshot.json .superpowers/r81/reproduced.json
```

Capture and reproduction also refresh the adjacent
`2026-10-03-marivo-r81-consumer-snapshot-data/` directory of three checked
zlib shards. Each shard is under 800,000 bytes so the repository's added-file
size hook accepts it individually.

When the ignored original baseline is unavailable, copy the exact embedded
snapshot.baseline object to a temporary JSON file and pass that file as
--baseline. The collector reads its metadata; it does not require the ignored
patches or historical copies. Reproduction requires the same input bytes,
capture HEAD, captured Python version, virtual-environment dependency versions
and public import probe.
No timestamp or machine-specific absolute root enters the snapshot. Historical
input hashes remain historical; --verify-current checks the captured source,
tests, disclosure, owners and collector against the current worktree.
The temporal support owner is included alongside the three other owning specs.
Snapshot and evidence index exclude themselves from input hashes to avoid a
digest cycle; the index records the final snapshot digest below.

The sharded zlib inventory has per-shard and concatenated-payload byte lengths
and SHA-256 checks, plus a checked uncompressed length and SHA-256. Concatenate
the ordered shards from inventory_payload.chunks, decompress with zlib and parse
the JSON to inspect source_files, symbol_dispositions,
test_files, legacy_test_dispositions, fixture_worker_dispositions,
disclosure_files, requirements and V_obligations. The collector scans tracked
and nonignored working Python files using AST definitions, imports, calls,
registrations/export/Help/SQL strings and Store/family branches, plus current
API, skills and latest English/Chinese disclosure. Every input has a file hash.

AST definitions are not parametrized pytest collection. File-wide aliases and
lexical calls are static candidates; reflection, receiver ownership, issued SQL
and dynamic source/codec reachability remain unverified. M rows and individual
symbols/tests have replacement/deletion/shared-owner duties; physical deletion
is not performed. Existing mathematical assertions are retained as
counterexample inputs, not automatically promoted to independent new oracles.

Import-only probes inspect actual exports/classes/signatures/native Help/admission
and installed dependency versions without Session/Run or business reads. They
confirm that target classes/methods are absent and the existing two-quantity
correlate default is still Spearman. There is no placeholder export, Help leaf
or type stub.

Integrated type/domain/time/key targets, all 779 Decimal(p,s) numeric-law
profiles, exact native candidates and V/journey scenarios are distinct scopes.
Each requirement has a stable ID, method/state/numeric/precision version,
ordered exact input types/core domains/keys/parts, route/backend/form/source-time
origin, responsibility, oracle and proof class. All future mandatory cells are
planned or blocked; executed=qualified=0. No unlisted Cartesian combination
inherits evidence. The 256 native expanded Pearson/Spearman cells are explicitly
blocked pending exact registrations and retained-input/numeric qualification.

Current prepared-observation gaps for complete grid transport and Decimal,
new certified_statistical physical declarations, complete run/Subject/pair/
training/future parts and closed nonempty Finding subjects remain mandatory
R8.2-R8.4 duties. Existing R7.9/prior skipped, failed or unverified targets remain
unchanged. R9/R10 backend/Agent/release duties remain separate.

## Actual validation

Final static inventory: 415 source files, 461 Python test files, 108 current
disclosure files, 449 per-symbol dispositions, 391 exact original test-node
dispositions and 11 fixture/worker dispositions. F01-F14, M01-M18 and V01-V20
all have existing owners or concrete future responsibilities. There are 53,695
mandatory requirement IDs: 51,207 planned and 2,488 blocked, executed=qualified=0.
Blocked consists of 256 expanded native route targets and 2,232 complete F11
grid/Decimal prepared-observation journeys. The remaining F11 int64/float64
Entity targets are planned, not inherited passes.

| Actual command | Exit | Evidence / limits |
| --- | --- | --- |
| `.venv/bin/python scripts/r81_static_freeze.py --baseline .superpowers/r81/baseline.json` | 0 | AST/current-import snapshot, checked compressed payload; static only |
| `make test TESTS='-n 0 tests/test_analysis_contract_freeze_r81.py'` | 0 | 13 passed, zero skips; coverage/IDs/owners/types/parts/keys/dispositions/inactive API and independent corruption rejection |
| `make typecheck TYPECHECK_TARGETS='scripts/r81_static_freeze.py tests/test_analysis_contract_freeze_r81.py'` | 0 | strict typing of both new modules |
| `make lint-agent LINT_TARGETS='scripts/r81_static_freeze.py tests/test_analysis_contract_freeze_r81.py'` | 0 | formatting, Ruff and actual import contracts |
| `make check-agent` | 0 | initial broad gate: 5,109 passed, 5 skipped; lint/import, 424 typed files and API docs passed |
| `make check-agent TESTS='-rs'` | 0 | final broad gate after expanded F11/worker duties and temporal-owner hash coverage: 5,109 passed, 5 skipped in 46.89s; 897 formatted files, lint/import, 424 typed files and API docs passed; -rs only expands skip reporting |
| `.venv/bin/python scripts/r81_static_freeze.py --verify-current` | 0 | every captured live input/owner/collector hash matches; no Runtime claim |
| snapshot regeneration to `.superpowers/r81/reproduced.json` and `cmp` | 0 | deterministic byte-for-byte reproduction from the same inputs and probe |
| `git diff --check` | 0 | no tracked whitespace errors; new files separately checked for trailing whitespace |
| preservation check recorded in `.superpowers/r81/preservation.json` | 0 | six protected on-disk hashes unchanged/untracked; external HEAD delta contains only those six deletions; initial staged diff empty; product/old tests/AGENTS/skills unchanged |

Raw successful receipts are `.superpowers/r81/{collector,targeted-tests,typecheck,
lint,check-agent,hash-verification,reproduction}.log`. Their final hashes and
snapshot digests are recorded below. These ignored receipts are local inspection
copies; the command/results above and captured metadata remain in tracked docs.

All five default-suite skips retain their original owning conditions:

| Original location | Count | Reason |
| --- | --- | --- |
| tests/test_datasource_metadata_schema_only.py:81 | 1 | owner implementation propagates channel failure to the dispatcher fallback |
| tests/test_lazy_row_expression_admission.py:230/264/289/322 | 4 | SQLite declares no decimal storage for this fixture |

No skip is R8 evidence. Runtime/release-marked cases excluded by the ordinary
default suite are not executed. No failure from the preserved R7.9 product
worktree was found. Iteration diagnostics were confined to the new collector/
test module: the initial import probe lacked inspect; initial typing/lint
diagnostics were repaired; symbol expansion exposed six tuple-length typing
errors and one C401 set-comprehension lint error. The latter failed receipts
remain `.superpowers/r81/typecheck-symbol-expansion-failed.log` (make exit 2)
and `lint-symbol-expansion-failed.log` (make exit 2). These failures are recorded
as repaired failures, not passes. An earlier 12-pass test run predates the added
per-symbol obligations and is superseded by the final 13-pass scope.

Final owner-hash coverage, Python-version capture and narrative completion flags
were followed by snapshot regeneration, hash/reproduction verification, a
13-pass narrow run and the final broad gate above. Final evidence accounting
changes only this index and does not broaden any product qualification.

No release-check, full Runtime suite, installed wheel, remote backend, MinIO or
real-Agent journey is run as part of this documentation/static task.

## Final captured hashes

| Item | SHA-256 | Bytes |
| --- | --- | --- |
| consumer snapshot | `b768dd67a0a3a354b2133c05ff323e0f0344f7f4d08ada372aa10f0b784349b5` | 39132 |
| compressed inventory payload | `zlib_chunks_v1`; concatenated SHA-256 is recorded by ordered chunk hashes below | 2269088 |
| uncompressed inventory | `79555db52d54decf828ffa6606bb97c5d3c95c5ce0560b39cf3ed6dad0bab179` | 70523092 |
| collector | `bc6d143dab0c33c451fb64515173ef55282922cb3048634b6ea0e4afda44689c` | 60312 |

| Snapshot payload shard | SHA-256 | Bytes |
| --- | --- | --- |
| `part-000.bin` | `516b561a112ac792517ee091e0281ac94291ce4610dda6d419d15df92084650b` | 800000 |
| `part-001.bin` | `f577e91a4a681226acb56ab50aa216ef0495551ef5b82e76c5badcb32c2bdb1c` | 800000 |
| `part-002.bin` | `b650ad1b19da73114631871e48cefb06162eb629afe398ceeb6a6f6bbdf06aef` | 669088 |

Owner hashes are the final snapshot.owner_inputs values (eleven current inputs);
entry historical hashes remain in snapshot.baseline. Python is
`3.12.13 (main, Mar  3 2026, 12:39:30) [Clang 21.0.0 (clang-2100.0.123.102)]`.

| Local receipt | SHA-256 |
| --- | --- |
| collector.log | `2887bffd83632aa4598d355328b97d1f5033a237183dbb7f6c1e3be1598d3fdf` |
| targeted-tests.log | `16301e51d56833b49ef79172d0b915607e5731ececaf62d68eb37532ba097375` |
| typecheck.log | `f9b031e5c45aa702cd4fef028d465551cb20a2c4698e0913c7b00766b20ac4d8` |
| lint.log | `bc9ec91421ce383deeef4b873f68e7db7194b6028a507731c015aae7e880000f` |
| check-agent.log | `56ad39a4d40ae3c490a4d2a93e1ec472f3f96861b677d8d9fd8186a2e3a3778f` |
| hash-verification.log | `3a5dd31b85a8124dea8233b2d3697f8eb195a0bee91493068fb73b891d973dc6` |
| reproduction.log | `2887bffd83632aa4598d355328b97d1f5033a237183dbb7f6c1e3be1598d3fdf` |
| preservation.json | `b411b53a3e31daed3fc3a85ea8f04b25d7bc045bc58c965dcb40a0144c3cd82d` |
