# R9.2 real source evidence

Date: 2026-10-04

Implementation is delivered with a blocked exit: **42 passed / 1 blocked** of
43 R9.2 requirements. The unchanged R9 denominator is 394; 351 later goals remain
unverified. [results-index.json](results-index.json) owns statuses and proof
attachments; [summary.json](summary.json) owns aggregate accounting. The original
R9.1 bundle is retained alongside the current `freeze/` candidate/owner snapshot.

## Implemented behavior

The source matrix binds 19 actual physical profiles and 24 per-backend risks to
collected Runtime nodes, independent fixture expectations and exact native-submit
observations. Tables/views, file formats, HTTP parameter/auth scope, namespace
collisions, Iceberg/memory connectors and two-shard Distributed profiles remain
distinct. Fixtures are UUID-owned and removed in `finally`; product reads use
restricted readers. Default pytest collection starts no services.

Three confirmed defects were repaired:

- Cursor close exceptions now mark the submission failed with `close_failed`;
  connection disconnection remains a separate observation.
- ClickHouse default native-row UTC datetimes regain UTC only with explicit UTC
  Arrow schema authority; other mismatches remain structured refusals.
- Missing selected optional drivers produce `DatasourceConnectionError` with a
  backend-specific installation repair; fresh-process tests enforce isolation.

SQLite NUMERIC affinity returns float for the required Decimal(18,6) fixture.
The typed decoder rejects it without implicit conversion. The original success
goal remains **blocked**, with an exact-carrier/retest release condition. A passed
rejection test is not an exact-Decimal success pass.

## Reproduction and evidence ownership

Explicitly start the dedicated profiles using `tests/multisource_environment/manage.sh`
and its existing lifecycle rules. Trino, local ClickHouse and cluster ClickHouse
are mutually exclusive. PostgreSQL/MySQL can remain ready during these groups.

```bash
MARIVO_TRINO_ANALYSIS_TEST=1 .venv/bin/python -m scripts.r92_source_qualification run --label trino --selection trino --directory /tmp/r92-new
MARIVO_POSTGRES_ANALYSIS_TEST=1 MARIVO_MYSQL_ANALYSIS_TEST=1 .venv/bin/python -m scripts.r92_source_qualification run --label local-postgres-mysql --selection 'not trino and not clickhouse' --directory /tmp/r92-new
MARIVO_CLICKHOUSE_ANALYSIS_TEST=1 .venv/bin/python -m scripts.r92_source_qualification run --label clickhouse --selection 'clickhouse and not distributed' --directory /tmp/r92-new
MARIVO_CLICKHOUSE_CLUSTER_TEST=1 .venv/bin/python -m scripts.r92_source_qualification run --label clickhouse-cluster --selection distributed --directory /tmp/r92-new
.venv/bin/python -m scripts.r92_source_qualification finalize --runs trino local-postgres-mysql clickhouse clickhouse-cluster --directory /tmp/r92-new
```

Each run preserves command, UTC times, exit status, candidate/diff/untracked hashes,
host/Python/driver versions, JUnit per-node outcomes and independently hashed
portable receipts. Finalization rejects stale candidates, changed attachments,
missing/duplicate IDs, skipped/failed owners and incomplete proofs. It never
starts services or overwrites an existing run/freeze.

Verify this delivery from the repository root:

```bash
.venv/bin/python scripts/r9_qualification_requirements.py verify --directory docs/superpowers/specs/2026-10-04-marivo-r92-evidence/freeze
.venv/bin/python scripts/r9_qualification_requirements.py verify-results --directory docs/superpowers/specs/2026-10-04-marivo-r92-evidence/freeze --results docs/superpowers/specs/2026-10-04-marivo-r92-evidence/results-index.json
make test TESTS='tests/test_full_algebra_backend_matrix.py tests/test_datasource_adapter_contract.py'
```

`accepted-checks.json` records the final candidate's narrow default gate (78 passed),
touched typing and `make check-agent` (5,914 passed / 5 skipped, API docs included).
`checks.json` and `final-checks.json` retain preceding candidates' successful checks.
The final changes strengthen authenticated HTTP fixture setup and frozen-candidate
validation; the unchanged site build is reused. Shared R1.2/R1.3 regressions have
separate logs and run metadata, with implementation/test file hashes allowing impact
review when only unrelated evidence tooling changes.
An early shared ClickHouse run was interrupted by an incorrectly timed profile
switch; its failed log is retained and grants no qualification. The corrected
run passed all eight tests; cluster and Trino regressions are separately recorded.
The first Trino control run exhausted its one-second setting-query budget under
concurrent test load; the subsequent serial run passed all three regressions.
Earlier development failures are described in
`diagnostics.log`; formal candidate receipts contain only completed selected runs.

## Evidence boundaries

The explicit environment binds PostgreSQL 17.11, MySQL 8.4.11, Trino 483
(Iceberg plus memory connector), ClickHouse 26.3 and the recorded local library
versions. `environment.json` binds pinned Compose/configuration hashes and the
two-shard/one-replica profile. The starting VM and all containers were stopped;
the task restores that state and preserves volumes and private credentials.
`cleanup.json` records completed stop commands and the final VM observation.
`after-services.log` was captured while MySQL's stop command was still finishing;
its transient healthy status precedes that command's successful completion.

Source profile tests cover metadata/schema, exact row identities, parameterized
filter/projection, empty fixed schema, one-row batches, early stop, fetch/decode/
close faults and connection exit. Fault injection follows actual driver submission
and delegates native close; it is local failure/cleanup evidence. Remote termination
remains `remote_unknown`, never `remote_confirmed`.

Type risks record exact int64 extrema/nullability, Decimal(18,6), native/parsed
microsecond carriers with repeated local DST labels, and composite revision keys.
They do not grant arbitrary precision/scale, report-calendar semantics, Analysis
method registrations, fixed recovery, global SQL closure, performance, installed
wheel, real-Agent or release acceptance.

Repository commit hooks normalized JUnit terminal newlines and log trailing
whitespace. `artifact-normalization.json` binds the original and formatted hashes;
`captured-artifacts-before-format.zip` preserves the affected original artifacts
and attachment metadata. Candidate content and qualification statuses are unchanged.
