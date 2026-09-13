# Slice 9c: Adversarial Runtime and read acceptance

Date: 2026-09-13

Status: Complete; 9d and release readiness remain pending.

## Candidate and boundaries

The approved Slice 9c plan starts from `b9805de94`, the committed Slice 9b
candidate. The working tree initially contained only untracked `evidence/`.
All 16,303 existing evidence files were hashed before changes. Acceptance uses
this macOS arm64/Python 3.12 host and two Runtime workers.

Current owning contracts are the accepted materialization and Session Runtime
Read designs. Ordinary fault and process acceptance uses local Parquet and
native DuckDB execution. S3 version, ownership and unknown-request behavior
uses SDK stubs with real Store files, including fresh-process proof loss.
Historical engine-storage and MinIO fault matrices are not current contracts.
No new API, Store generation, backend support, compatibility path, real-Agent
journey, commit, push or publication is included.

## Acceptance ownership

| Requirement | Assertion owners |
| --- | --- |
| Admission before live work; atomic failure and authoritative success | `test_lazy_materialization_failures`, `test_lazy_materialization_store`, `test_lazy_materialization_runtime_acceptance`, `test_lazy_adapter_failures`, `test_lazy_public_read_acceptance` |
| Same-Session contention and independent Session progress | `test_lazy_runtime_concurrency`, `test_lazy_worker_recovery`, `test_lazy_reconciliation_snapshot` |
| Real process death, uncertain readback and termination | `test_lazy_adapter_crash_acceptance`, `test_lazy_worker_recovery`, `test_lazy_object_storage_contracts` |
| Required parts and exact backing authority | `test_lazy_retained_failures`, `test_lazy_materialization_storage`, `test_lazy_lifecycle_runtime_acceptance`, `test_lazy_lifecycle_object`, producing-family failure/publication tests |
| Explicit cross-Session input and scoped reads | `test_lazy_public_read_acceptance`, `test_lazy_runtime_read_acceptance`, `test_lazy_runtime_reads`, `test_lazy_session_history`, `test_lazy_session_graph`, `test_lazy_finding_reads` |
| Independent Artifact/storage/Evidence inspection | `test_lazy_integrity_inspection`, `test_lazy_inspection_boundaries`, `test_lazy_read_integrity_regressions`, `test_lazy_object_access_boundaries` |
| Generation rejection, removed public paths and redaction | `test_lazy_public_session`, `test_cutover_removed_contracts`, `test_lazy_binding_cold_acceptance`, Finding codec regressions, current-surface audit |

The new public journey produces two real Delta Findings, consumes the exact
Artifact from a second Session with its source offline, and cold-recovers both
bindings in a third interpreter. It checks original Finding ownership, external
graph boundaries, selected-page corruption, and full inspection under a writer
guard. Existing process and protocol tests retain their own timing assertions.

The opt-in `tests.lazy_adversarial_capture` observer runs after fixture teardown.
It records selected Store identities, outcomes, counts and digests, never row
payloads, source parameters, SQL or credentials. It does not reconcile or change
execution dispatch. Test assertions own transitions; post-test observations
alone are not proof of admission ordering or intermediate isolation.

## Repairs and disclosure

Public selected Finding corruption reproduced a native JSON exception retained
in the structured error's cause/context. JSON encoding/decoding, Finding scalar
decoding and selected commit-time parsing now raise safe errors outside native
exception handlers. Independent malformed JSON, decimal, date, datetime and
timestamp regressions pin that boundary without changing error types or fields.

Current Runtime/Evidence specs and both latest Evidence site pages incorrectly
described the storage integrity axis as semantic authority. They now describe
Artifact, storage authority and Evidence integrity, matching the existing type
and owning design. Historical release notes remain historical.

The wider diagnostic exposed twenty stale test outcomes across two runs:
fourteen still targeted removed engine-storage events, receipts, budgets or
rejections, and six assumed an implicit pandas worker or an older Session
creation signature. They are updated to actual Parquet reservation/file-creation
crashes, exact file mutation, current disk limits, admitted native membership,
explicit registered pandas selection, and the full current creation signature.
The obsolete invalid-default-local-target parameter is removed: default local
success is covered by the public Session tests, and missing explicit object
configuration retains its pre-source rejection test. Adapter repairs passed
29 cases and recovery repairs passed 28 cases before the final gate.

Strict explicit-path mypy diagnostics were compared with the committed version
of the public Session test using `--shadow-file`. Nine diagnostics are unchanged
in the existing shared fixture module and untouched calendar test; the new test
and worker modules and modified production modules pass their focused checks.
The repository's required full typing gate remains mandatory. The evidence
records the comparison rather than changing unrelated fixture/calendar typing.

## Evidence and completion gate

Evidence lives under `evidence/slice-9c/2026-09-13-adversarial-runtime/`.
Command receipts bind before/after source manifests, exact commands, timestamps,
exit status and log hashes. Preserve failed attempts and independently verify
the initial historical evidence inventory at handoff.

Run focused default and Runtime checks, the complete selected 9c matrix and
affected 9b Runtime checks, then `make check-agent`, `make release-test`, site
content/build checks and whitespace validation on the final candidate. Retain
installed-package reports and tested distribution bytes. Changes invalidate
affected evidence and require reruns; status-only closeout changes are recorded
separately. Completion of this unit does not complete 9d or authorize a release.

## Final acceptance

Tested candidate SHA-256:
`d0ac157fe6ccf872890ba8b2ccb44d6d278aeb0b2bafb25532782c169dc931da`.
The manifest covers 1,195 tracked and non-ignored new files, excluding evidence.
Every final gate has this same before/after digest. `acceptance-index.json`
maps the nine requirement groups to 5,741 distinct passing test nodes and their
receipts; it also verifies all 16,303 historical evidence files byte-for-byte.

| Final gate | Result |
| --- | --- |
| `make check-agent` | Passed: 4,848 default tests, full lint/import contracts, typing of 332 source files, and API documentation |
| Selected Runtime matrix, two workers | 892 passed in 1,080.13 seconds, including affected 9b paths and all selected 9c adversarial/read owners |
| Captured bindings and cold recovery | 1 passed; distinct source values, scope exit and fresh-process exact-key recovery |
| `make release-test` | 29 passed; nested wheel checks passed 376 contracts, five Runtime examples and the three-process production/offline-continuation/recovery journey |
| Latest bilingual site | `npm run verify:content` and `npm run build` passed |
| Focused explicit-path typing | 15 changed/new modules passed; the nine unchanged baseline diagnostics described above remain separately recorded |
| Current-surface negative audit | Zero forbidden current-package/spec/latest-site hits; historical release notes excluded |
| Preserved-project readback and whitespace | Passed in a fresh process; `git diff --check` passed |

The public journey used independent processes 41682, 41752 and 41850. Artifact
`artifact_58963200f71d4be9b4d7decdae16e0bb`, its Evidence digest and both original
Finding identities survived cross-Session use and cold recovery. Original Delta
values remained `[5.0, 13.0]`; the consuming Session published its own one-row
Artifact with `[13.0]`. The cold phase recorded zero source fences for both
Sessions. Corrupting the unselected Finding left the selected page readable;
selecting the corrupt record raised a safe error, while full inspection reported
`valid / readable / invalid` across Artifact/storage/Evidence axes.

After the matrix, process 45768 read both Artifacts from the copied synthetic
project with the source unavailable. Both full inspections were valid and
readable. Store counts remained two Runs, two Artifacts, two Evidence records,
three Findings and zero resource obligations; no origin call or execution
statement occurred. Exact identities are in `public-read-journey.json` under
`runtime-final-v3/hooks/` and `preserved-readback.json`.

The tested distributions are retained in `tested-distributions/`:

- wheel SHA-256:
  `92ffa81b0982132b9e11583311edd4a74766fcab6b112af0212f4e61eb3d2a91`;
- sdist SHA-256:
  `60e849c83b01fcb29d68fc2184a06c6ce53b8021ca7c4f02970d49ed16e378a5`.

Installed acceptance verifies archive contents, import origins, rejection of a
foreign source import, and fresh-process recovery. The first packaging attempt
failed on the configured mirror's TLS EOF before building. The passing retry
used `PIP_INDEX_URL=https://pypi.org/simple` for that command only.

## Failed attempts and handoff

All diagnostic attempts remain in evidence. The initial public corruption test
exposed the native exception chain; its first fixture/setup failures are recorded
in `initial-diagnostics.json`. The first wider Runtime run was stopped after
fourteen stale test failures, and the separate diagnostic found the six other
stale outcomes described above. The first broad gate exposed the observer's
collision with a test-owned `Path.open` monkeypatch; moving observations after
fixture teardown repaired it, followed by the complete passing broad gate.

`runtime-final-v2` passed 891 cases and failed only when the Event journey tried
to write its report to the absent `hooks/7c` directory, after its behavioral
assertions had passed. The evidence runner now creates its configured output
directories. A focused retry passed, followed by the complete 892-case
`runtime-final-v3` run on the unchanged candidate; the earlier run is not counted
as a passing matrix.

Compared with 9b's accepted source manifest, only the three metadata/Finding
codec modules changed in the package. Successful algebra, backend selection,
source connectors and storage I/O code remain byte-identical. The affected 9b
Runtime paths, SDK boundaries and installed checks were repeated. This record
makes no new backend-support or live-S3 crash claim; real-Agent journeys and
release readiness remain owned by 9d and the release gate.

Cleanup verified no live process referenced any owned test root, then removed
the 24 temporary roots recorded by completed gates. Retained projects and all
historical evidence remain intact. Only this execution record and the parent
cutover plan receive completion text after the tested candidate. `handoff.json`
records that exact difference, and `attachments.json` inventories the final
evidence files. `tested-changed-sources.zip` and `tested-tracked.patch` preserve
the tested changes before closeout. No commit, push or publication was performed.
