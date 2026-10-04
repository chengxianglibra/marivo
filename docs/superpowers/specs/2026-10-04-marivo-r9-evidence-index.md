# R9.1 static evidence index

Date: 2026-10-04

The [qualification ledger](2026-10-04-marivo-full-algebra-dsl-r9-qualification-ledger.md)
explains coverage and remaining bindings. [index.json](2026-10-04-marivo-r9-evidence/index.json)
contains independent 512 KiB shard hashes, ordered bundle hash, decoded payload
hash and scenario count. Attachments are portable repository files.

The payload retains candidate file/dependency/diff/untracked hashes, owner digests,
all 9,109 seeds, historical row references, many-to-many target mappings, physical
profiles, actual method declarations, test-function inventory and initial results.
Only explicit current scenario IDs count in the acceptance denominator. Deterministic
packing and hash checks are static integrity evidence, not method qualification.

Run from the repository root:

```bash
.venv/bin/python scripts/r9_qualification_requirements.py summary
.venv/bin/python scripts/r9_qualification_requirements.py verify
make test TESTS='tests/test_full_algebra_backend_matrix.py'
make typecheck TYPECHECK_TARGETS='--explicit-package-bases scripts/r9_qualification_requirements.py tests/test_full_algebra_backend_matrix.py'
make lint-agent LINT_TARGETS='scripts/r9_qualification_requirements.py tests/test_full_algebra_backend_matrix.py'
```

`generate --directory <new-directory>` creates a new freeze. Accepted historical
bundles must not be overwritten; scope revisions retain old/new IDs and basis.
`verify-results --results <result-index>` checks later evidence bindings. Default
collection and these commands start no backend service. Exact Runtime selectors
remain unbound until follow-up packages supply actual fixtures/services/assertions;
no guessed selectors are published.

Completed check logs and hashes are stored in `checks.json`. The narrow tests cover
seed/method mapping without Cartesian expansion, required routes and form differences,
SQL/risk obligations, duplicate/missing targets, candidate/owner drift, false passes,
incomplete Runtime proofs, orphan results/shards and deterministic/damaged packing.
No full Runtime, release-check, MinIO, installed package, cost or Agent run is claimed.

## Completed static exit

The frozen denominator is **394 scenarios**, all unverified; target Runtime
executed=passed=0. All 9,109 seed profiles remain in the traceability catalogue.
The [initial result index](2026-10-04-marivo-r9-evidence/results-index.json) is
bound to the same candidate and grants no qualification.

The [completed checks](2026-10-04-marivo-r9-evidence/checks.json) record exact argv,
UTC start/end times, exit codes, candidate digest and portable log hashes:

- Matrix integrity: **13 passed**.
- Targeted typing/lint, snapshot and result-index verification: exit 0.
- `make check-agent`: exit 0, **5,907 passed / 5 skipped**, including lint,
  import contracts, 412-source typing and API documentation build.

An earlier narrow check incorrectly required 55 source rows for 55 SQL IDs.
DS15 also occurs in a status overlay. The corrected assertion checks 55 distinct
audit targets while retaining every source reference. Its failed log and candidate
binding are preserved in `matrix-draft-failed.log` and `checks-draft-failed.json`;
they grant no pass and do not replace the completed checks above.
