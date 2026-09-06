# Injectable datasource credentials — implementation acceptance

Date: 2026-09-06

Status: accepted in the local checkout.

Design: [injectable datasource credentials](../specs/2026-09-05-injectable-datasource-credentials-design.md).
Base revision: `f93493f32bfa881f40b3a41b5cfbf0962f378f40` plus the local implementation.
This is local checkout evidence, not a release or host-plugin deployment.

## Delivered boundary

- `md.credential_scope(resolver=...)` selects a synchronous host resolver for new
  operations and connection runtimes. It does not own connection lifetimes.
- Explicit resolution neither reads nor writes the default env/cache chain.
  Requests group a reference's fields and carry the bound project and operation budget.
- Session and semantic reader runtimes keep the resolver after scope exit. Managed
  connection acquisition checks a different explicit resolver, including cache hits.
  External overrides retain their dispatch and original resource ownership.
- Public raw backend handoff remains unchanged. There is no backend proxy,
  scope resource registry, automatic scope cleanup, or execution authorization layer.
- Worker contexts carry the captured source and project. Late credential results
  cannot open a backend; late backend results are closed instead of published.
- Credential errors and test failure codes teach host-side repairs. Secret wrappers,
  managed error paths, help, persistence and telemetry do not disclose fixture secrets.

The implementation also binds auxiliary source-health inspection and catalog
connection/test operations to their actual owning project and resolver.

## Acceptance mapping

The focused tests live in
[tests/test_datasource_credentials.py](../../../tests/test_datasource_credentials.py).
The existing datasource secret, timeout, runtime, semantic, analysis, Help,
fixture and public-surface suites remain part of the full repository gate.

| Design cases | Evidence |
|---|---|
| R01 | Existing default secret-cache and runtime tests; unreferenced datasource test. |
| R02 | Public connect/test/no-persist calls with an env canary and a cache accessor that fails on any access. |
| R03–R04 | All six typed reasons, invalid return variants, raw SDK payload and cause suppression. |
| R05 | Actual DuckDB `md.connect` and `md.test` roundtrips with injected bearer material. |
| R06 | Public inspection, snapshot acquisition, semantic preview and source-health checks. |
| R07 | Real `Session.observe`, auxiliary timezone acquisition and positive materialized frames. |
| R08 | One reference shared by Trino user/auth fields is resolved once; captured engine kwargs agree. The Trino connection factory is a test double. |
| R09–R10 | Cache-hit mismatch, default-runtime mismatch, nested restoration, concurrent resolver contexts and surviving connections after scope exit. |
| R11 | Public deadline workers receive the resolver and bound project; catalog and no-persist operations remain bound after ambient project changes. |
| R12 | Event-controlled resolver timeout and late connection cleanup; no random sleeps. |
| R13 | Session materialization after the selecting scope exits. |
| R14 | Same-process resume with a new resolver and fresh-process resume with credentials supplied through stdin. Persisted state is scanned for fixture secrets. |
| R15 | Fresh connection re-resolution after credential rotation; existing backend reuse remains unchanged. |
| R16 | Local backend override plus Marivo-built connection, and independent backend-factory dispatch. |
| R17 | Static describe/catalog/Help calls and an unreferenced datasource never invoke the resolver. |
| R18 | Secret repr/str, copy/pickle rejection, provider and driver tracebacks, query failures, Help, telemetry and project-state canaries. |
| R19 | Standalone child process executes public `md.test` using stdin-only credential material. |
| R20 | Driver-open and query failures retain backend meaning; successful material retrieval is not reported as missing credentials. |
| R21 | Both raw-backend handoff forms retain original identity and normal connection cleanup. |

A separate real HTTP fixture accepts only the expected Bearer header. DuckDB reads
its JSON response through an injected datasource connection and returns the expected
aggregate, `30`. The fixture clears proxy variables for its loopback request;
production proxy settings are not modified by Marivo.

The cold-resume test explicitly releases the independent parent catalog's connections
before launching the child, as required by DuckDB's file-lock ownership. Exiting a
credential scope does not release those connections.

## Review repairs

- Analysis source materialization redacts driver messages and suppresses the raw
  exception context while preserving `SemanticRuntimeError`, its kind and refs.
  A public `Session.observe` regression checks the message, traceback, logs and
  project state using an injected canary.
- Metadata table-resolution errors are classified before redaction. The real
  Trino classifier is exercised with a controlled backend: permission denial
  retains projected-schema fallback; missing tables and unprojected sources still
  fail closed. Returned warnings and errors exclude the injected canary.
- Raw SQL owns query-error redaction and keeps `DatasourceRawSqlError`, observed
  query effects and its repair target. The connection service preserves these
  typed operation errors. Default and injected modes exercise the same failing
  public query path.

The focused credential and projected-inspection suites passed: 46 tests.

## Validation

- Targeted credential, datasource, semantic, Help and example suites passed during iteration.
- Post-review `make check-agent`: passed; 5,550 tests in 108.96 seconds, import/lint checks, typing for 312 source files, and API documentation. This count includes the independent semantic-definition changes already present in the checkout.
- `npm run verify:content`: 343 required site files verified.
- `npm run build`: Astro check/build, API generation and install-script verification passed; 321 pages built.
- Design Markdown structure, local links, code-example syntax and `git diff --check` are checked at closeout.

## Explicit limits

No remote Trino, MySQL, PostgreSQL or ClickHouse account was exercised. No DSH source
was changed, and no plugin or production host adapter was deployed. The runtime
cannot forcibly kill an uncooperative Python callback, revoke an already-exposed raw
backend, or control external driver logging. Those remain the design's stated host
and execution-environment boundaries.
