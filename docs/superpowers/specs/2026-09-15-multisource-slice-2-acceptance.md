# Multi-datasource Slice 2: exact backend dispatch

Date: 2026-09-15.

Status: complete. Implementation, independent review, focused and broad checks,
and site validation passed. Only DuckDB is enabled. This record does not
establish remote backend acceptance.

## Baseline and implementation

HEAD at start: `01a8403f8032a63073a9f4590401e60efbe1a284`, branch `lazy-dataset`.
The worktree already contained the uncommitted Slice 1c changes and its evidence.
An exact pre-task archive at `/tmp/marivo-slice2-baseline/files.tar` and the
preexisting diff were used to separate this slice from those changes. No commit,
push, dependency upgrade, external service startup or release is included.

- `ImplementationRegistration` now contains an immutable collection of exact
  backend registrations. Duplicate backend keys and empty operations are
  rejected. The existing typed invocation determines method/shape eligibility;
  only existing DuckDB builders and validators are registered. This is not a
  universal remote builder or capability framework.
- Complete source execution and correlation/distribution preparation are
  independently declared. Each `SourceStep` carries the selected registration
  and one operation; mismatching operations and bindings are rejected. Kendall
  and distribution Shapley preparation do not imply full-source eligibility.
  Existing local-method admission is unchanged.
- Retained Parquet attaches to a source only through the existing DuckDB reader.
  Other source candidates do not acquire retained input authority or trigger an
  upload. Native retained stages and admitted local multi-input work remain.
- Runtime checks the selected backend against its binding and datasource before
  opening the source. Selected errors do not cause replanning or resubmission.
  Known unsupported roots fail before Run admission with bounded method, backend
  and shape guidance. Physical schema validation stays before source-row work.
- Source-domain checks retain all captured owner identities; statement/resource
  execution-context checks remain separate. Neither uses engine versions. A
  binding hit precedes placement, profile/credential resolution and connections.
  Public signatures, logical identity, execution keys and Store v5 are unchanged.
- Focused native Help, current analysis/planner docs, workflow guidance and both
  latest site languages disclose the existing backend boundary.

## Initial verification

| Check | Result |
| --- | --- |
| Pre-change placement/correlation baseline | 28 passed |
| Existing placement/correlation/candidate/forecast checks after migration | 84 passed |
| Final dispatch, authority, placement, correlation and disclosure selection | 92 passed |
| Distribution, correlation and execution-economics Runtime, one worker | 45 passed in 108.12s |
| Expanded Runtime, code frozen, one worker | 98 passed in 144.89s |
| Touched source/test typing with explicit package bases | 17 files passed |
| Touched formatting, lint and import contracts | Passed |
| `make check-agent` | Formatting/lint/import contracts, typing of 333 source files, 4918 default tests in 96.94s and API docs passed |
| `npm --prefix site run verify:content` | 343 required files verified |
| `npm --prefix site run build` | Astro checks/build and standard/Chinese install-script verification passed |

The 92-case selection was:

```sh
make test TESTS='tests/test_lazy_backend_dispatch.py tests/test_lazy_dispatch_authority.py tests/test_lazy_local_placement.py tests/test_lazy_correlation_contracts.py tests/test_lazy_disclosure.py tests/test_analysis_disclosure_journeys.py'
```

The first Runtime selection was:

```sh
make runtime-test TESTS='tests/test_lazy_distribution_runtime.py tests/test_lazy_correlation_runtime.py tests/test_lazy_execution_economics.py' RUNTIME_WORKERS=1
```

New registry/placement tests cover exact multi-entry selection without I/O,
duplicate rejection, unknown methods, preparation-only eligibility, local shape
restrictions and current Help. Test-only PostgreSQL registration exercises pure
dispatch; it is not a remote execution test. Retained transport admission uses
an injected physical candidate and inspects the actual Runtime binding callback;
it does not bypass semantic compatibility to claim a successful mixed query.

Independent authority tests vary Session, Store, catalog, semantic registry,
sidecar, parameter-binding scope and action port separately, plus datasource and
backend. They separately cover retained owner/digest/domain identity. A real
DuckDB binding-hit test removes the source file, forbids all source-access seams
and placement, and checks identical Artifact/Run identities and unchanged Store
counts. Existing real-driver tests cover foreign, forged and closed contexts.

## Review and iteration notes

The independent `review_slice2_plan` agent reviewed the actual increment against
the pre-task archive. The separate `slice2_authority_tests` agent authored and
ran the authority tests (13 default cases and one real Runtime case), with
focused Ruff and typing checks passing.

An expanded Runtime run found an existing exception-expectation defect:
`test_attribution_failure_or_cancellation_is_atomic[before_commit]` injected
`OSError` but expected `KeyboardInterrupt`. The reviewer reproduced it against
the isolated pre-task archive (one failure in 4.97s, verified baseline imports).
The test now expects the injected exception for each parameter; its atomicity
assertions remain. No production exception conversion was added.

An intermediate three-process candidate-hash check caught source/test edits
during its run. It passed in the 98-case rerun with code frozen; the hash assertion
is unchanged.
Initial new test fixtures were corrected to exercise their intended admission
phase rather than fail earlier at semantic shape/Metric compatibility checks.

The expanded Runtime selection was:

```sh
make runtime-test-agent TESTS='tests/test_lazy_backend_dispatch.py tests/test_lazy_dispatch_authority.py tests/test_lazy_attribute_operand_runtime.py tests/test_lazy_event_comparison_runtime.py tests/test_lazy_retained_runtime.py tests/test_lazy_local_execution.py tests/test_lazy_duckdb_execution_adapter.py tests/test_lazy_materialization_execution.py' RUNTIME_WORKERS=1
```

The initial independent review found no unresolved code issues. Initial source/test
diff SHA-256 against the pre-task archive (before the review follow-up below):
`87b3de1988145a2c45544b055c1f2ee7e6008b24bf01db826c09e9b54486f03e`.
This digest covers only the task increment, including the two new test files;
preexisting Slice 1c work is excluded.

## Remaining scope

PostgreSQL, MySQL, SQLite, Trino and ClickHouse production registrations remain
absent. Their real connections, physical types, method-specific builders,
transport and cleanup require their subsequent enabling slices. This slice does
not complete the older Slice 9 acceptance or constitute release readiness.

## Review follow-up

The subsequent supplied review identified two confirmed error-guidance defects.
Both were independently reproduced in an isolated copy with the pre-review
production files and the new regression tests (verified local imports): duplicate
registration failed in 0.90s with a compiler/source-recipe repair, and unplaceable
distribution preparation failed in 3.90s with full-source repair wording.

- Registration construction now raises the existing `DatasetRegistrationError`
  with `dataset.implementation_registry` location, the actual backend/method,
  and a repair to omit an empty entry or merge duplicate backend declarations.
- Placement retains the owning distribution-preparation rejection and its cause,
  and derives repair from full-source versus preparation-only registrations.
  It no longer describes a preparation-only registration as full source execution.
- SourceStep validation uses a positive operation-capability check. Runtime
  binding agreement and the DuckDB activation pin are separate readable guards;
  both protections remain.
- New checks cover Pearson/Spearman registrations that legitimately support both
  source and preparation, and source-free `contract()` routing to the same bound
  execute Help for DuckDB and an unsupported backend. A continuation is a valid
  callable, not a claim of backend admission or live connector verification.

Other suggestions were assessed as follows:

| Suggestion | Decision and evidence |
| --- | --- |
| Remove names of unenabled backends | Keep the closed names: multi-backend dispatch is this slice's explicit purpose; production registrations still contain only DuckDB. |
| Remove repeated backend lookup | Leave the small pure lookup unchanged; no correctness defect was established, and it does not justify another interface migration. |
| Merge retained import with enabled execution | Keep separate: a future source implementation can be enabled without permitting retained-Parquet import. |
| Require preparation=None for a source step | Reject: source and preparation are independent capabilities; current Pearson/Spearman support both. |
| Remove scattered registration test migrations | Keep required call-site changes; no compatibility alias is introduced. |
| Revert the forecast export assertion hoist | Keep the type narrowing: the touched-test typing gate reported an object-typed membership check after the loop. Moving the existing assertion outside it preserves behavior and satisfies the required gate. |
| Withdraw documentation-increment claims | Reject after comparing the exact pre-task tar: analysis/planner specs, workflow skill and both site languages contain actual Slice 2 additions, independently confirmed. |
| Remove datasource backend cross-check | Keep: actual datasource/backend agreement protects the selected implementation before connection. |
| Add a generic per-backend shape/type framework | Keep the existing typed invocation and method validators as owners; no additional backend capability is enabled by this slice. |

The independent reviewer reproduced the two defects and found no remaining
issues in the correction. The initial verification above describes the
pre-follow-up candidate.

| Follow-up check | Result |
| --- | --- |
| Dispatch, placement, correlation and continuation disclosure | 56 passed in 7.62s |
| Dispatch/authority, distribution, correlation and execution-economics Runtime | 53 passed in 104.34s, one worker |
| Touched typing | 4 files passed |
| Touched lint, formatting and import contracts | Passed |
| Final `make check-agent` | Lint/import contracts, typing of 333 source files, 4921 default tests in 98.95s and API docs passed |

Follow-up Runtime command:

```sh
make runtime-test-agent TESTS='tests/test_lazy_backend_dispatch.py tests/test_lazy_dispatch_authority.py tests/test_lazy_distribution_runtime.py tests/test_lazy_correlation_runtime.py tests/test_lazy_execution_economics.py' RUNTIME_WORKERS=1
```

Reviewed source/test increment SHA-256 against the original pre-task archive:
`5b23acbbae24fdea82f14c6b3bc5ade0cb124c81c358ae03beaf93a7021f0d3b`.
