# Slice 9a: Deterministic and installed-package acceptance

Date: 2026-09-12

Status: **Slice 9a complete**. Slices 9b-9d and release readiness remain pending.

## Outcome and prerequisite

Run every Slice 9a repository, numerical, public-contract, Help/example,
bilingual documentation/site and distribution gate on one identified candidate.
The accepted Slice 8c implementation is now committed at
`df633187b99c00ef6af323e6c9a646d8d33db910`. All 1,185 final source-manifest
entries and 167 indexed attachments were freshly verified before this slice.

## Contract, ownership and environment

The parent public-cutover plan owns this acceptance unit; the accepted Dataset,
Observation, operator, domain, semantic and Runtime designs continue to own
behavior. Reuse existing independent contract/numerical tests, current bilingual
examples and the installed-wheel probe. Do not introduce a parallel test registry.

The approved scope is this macOS host with Python 3.12.13, a real DuckDB
datasource and local Parquet results for the focused examples and installed
journey. No Python-version or operating-system matrix is claimed.

Owned changes are this record, its precise `.gitignore` exception, the parent
status/acceptance links and any demonstrated defect repairs. There are no planned
public exports, callable/type contracts, Help routes or persistence changes.
Preserve all prior evidence and unrelated work.

## Required gates and repair policy

1. `make check-agent` for all default tests, numerical references, typing, lint,
   import contracts and API documentation.
2. `make test TESTS='tests/test_lazy_disclosure_examples.py tests/test_cutover_documentation_examples.py'`
   and the same selection with `make runtime-test`.
3. `make docs-api`, then `npm run verify:content` and `npm run build` in `site/`.
4. `make pypi-build pypi-check`, then the complete `make release-test`.
5. `git diff --check` and candidate/previous-evidence integrity verification.

The non-editable wheel gate checks archive membership, actual import origins,
public exports/signatures/Help and Dataset protocols, retired paths and Store
generations, executable current examples, and three-process public production,
source-offline continuation and exact recovery. Preserve the final tested wheel
and sdist, installed reports and source/package hashes.

Repair concrete failures in their owning implementation or test and synchronize
affected disclosure. Re-run affected checks and the full `make check-agent` after
repairs; rebuild and repeat installed acceptance when package contents change.
An unresolved product contract returns to its accepted design owner. No required
failure is waived by a narrower passing result.

## Evidence and exit boundary

Evidence lives in `evidence/slice-9a/2026-09-12-deterministic/`. Each command has
its environment/cwd, timestamps, exit status, log hash and before/after candidate
fingerprint recorded. The candidate uses HEAD plus a sorted file-content
manifest, including owned untracked documentation. Failed attempts remain.

Close only after every required gate passes on the final executable candidate.
Record post-gate status-only documentation separately. Marker deselections and
unexecuted acceptance units are not passing tests. Handoff to 9b-9d does not close
parent Slice 9, prove other platforms/backends or authorize release. No complete
`release-check`, MinIO startup, commit, push, publication, user-global Marivo
installation or historical Session cleanup belongs to this task.

## Completed acceptance

Every required gate passed without an implementation or test repair. The tested
candidate fingerprint is
`1daa6834c32ec6da7bc6604bacfe2f5f55c89ece5f6c2cccfa5616171edf9360`,
covering 1,186 files at the baseline HEAD plus this record and its ignore
exception. Each accepted gate has identical before/after manifests and unchanged
symlink targets. No library code, tests, public disclosure, packaged skills or
persisted schemas changed.

| Gate | Fresh result |
| --- | --- |
| `make check-agent` | 4,824 passed; 332 source files typed; formatting, lint, import contracts and API docs passed |
| Default numerical coverage | 329 tests in 14 `*_numeric.py` modules included in the passing full default gate |
| Focused default Help/documentation examples | 85 passed |
| Focused Runtime Help/documentation examples | Three passed with real DuckDB and local Parquet |
| `make docs-api` | Sphinx warnings-as-errors build passed |
| Site content | 343 required files verified across current and versioned English/Chinese documentation |
| Site build | Astro check: zero errors, warnings or hints; 321 pages built; two-language search and both install-script outputs verified |
| `make pypi-build pypi-check` | New wheel/sdist built; metadata and wheel-content contract passed |
| Complete `make release-test` | Rebuilt and checked distributions; all 29 release tests passed |
| Nested non-editable installed contracts | 361 passed; 100 ordered exports/signatures/Help entries match source; Dataset state, forbidden paths and generation rejection covered |
| Nested installed Runtime examples | Five passed, including public sessions, native examples and current bilingual tutorials |
| Installed cold journey | Three distinct processes; production, source-offline continuation and exact recovery; Run counts 1, 2, 2 |
| Whitespace and integrity | `git diff --check` passed; all 785 prior evidence files preserved |

These counts overlap and must not be added. `default-coverage.json` maps the
default collection to modules; the passing `check-accepted` receipt provides its
execution proof. Default collection excludes 987 Runtime, release or object
connection cases by the existing marker policy. The focused and installed gates
select their own markers; these deselections are not skipped acceptance cases.
Full backend/economics, adversarial and real-Agent acceptance remain 9b-9d.

The installed probe reused its existing `MARIVO_SLICE8C_EVIDENCE_DIR` capture hook
with a Slice 9a destination. It installed actual wheel bytes with separately
installed, version-constrained dependencies outside the checkout, verified loaded
module and distribution origins, checked dependencies and rejected deliberate
source-path injection. Both archives match all 328 owned package files.

The public journey produced `artifact_f312c90358384ed5ac7a75715b09a3bf` in
`run_4ab6f046f21028991d2b11b9`, under
`session_5db2364192854fba96f56c61b0fa434b`. Its Evidence digest is
`0bf2c224de24a4c958592886cc020e3b4c9d8aed3cc75c7cf6ac2a0d22a7bb14`.
Current/baseline/delta values are 30/12/18. All three processes read the same
Artifact, producing Run, Evidence and Finding identity, with valid integrity and
readable storage. Source-offline rank/limit uses retained Parquet; exact recovery
records zero execution statements and no additional Run. Native Parquet
statements during continuation are not origin-source queries.

## Diagnostics and final handoff

All required acceptance commands succeeded on their first attempt. The retained logs
include dependency deprecation notices from installed Ibis/DuckDB and Pagefind's
existing treatment of two non-page HTML fragments and Chinese stemming. These
are not ignored failing gates; the required build and test commands exited zero.
Package installation used official PyPI through per-command overrides without
changing machine configuration. The capture helper and selected environment
versions are retained with the receipts.

A supplemental handoff command initially treated `git diff --no-index` exit 1
(expected for the new record, with no whitespace diagnostics) as a failure,
which prevented patch creation and caused the following patch check to fail.
Both diagnostic receipts remain; corrected patch assembly and the final patch
check supersede that attempt. This required no implementation or test change.

The final tested `0.5.3.dev0` distribution is retained under `distribution/`:

- wheel SHA-256: `ae44239cba1d4c2c884b7cf9feb778723861ba468f0a0890243f9fa24bdb182c`;
- sdist SHA-256: `7e49204291733da4e4e671985228452ed5d55484485a3f0236739780d9ce5065`.

The [final handoff](../../../evidence/slice-9a/2026-09-12-deterministic/handoff-final.json)
binds the tested candidate, final source manifest, complete owned working-tree
patch, tested distribution copies, accepted command receipts and installed
terminal reports. Its artifact index hashes the retained attachments. Manifest
fingerprints are SHA-256 over sorted `path`/`sha256` records serialized as JSON
with `sort_keys=True` and separators `(',', ':')`; symlink targets are recorded
separately.

Only completion text in this record and the parent status/acceptance links follow
the passing executable gates. The final manifest records those differences
explicitly; executable and package inputs remain identical. Slice 9a is complete
for the approved macOS/Python 3.12 environment. Parent Slice 9 and release
readiness remain open until 9b-9d pass for the same executable candidate.
