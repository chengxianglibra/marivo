# Slice 1d correction: capability restoration and unified execution ownership

Date: 2026-09-15.

Status: complete. Capability restoration, unified execution ownership, independent
review, focused Runtime acceptance, broad checks and site build passed.

## Scope and baseline

HEAD: `01a8403f8032a63073a9f4590401e60efbe1a284`, branch `lazy-dataset`.
The pre-1d workspace was reconstructed from HEAD plus the saved
`/tmp/marivo-1d-baseline.patch`, without resetting the working tree. Earlier
Slice 1a–1c and Slice 2 changes and pre-existing untracked evidence remain.
The original 1d record is preserved and marked superseded. No commit, publication,
remote activation, dependency change or new private-state transfer is included.

## Restored behavior

- DuckDB datasource/entity readers: Analysis JSON, JSON POST, custom HTTP auth,
  extensions and retained object-stream registration.
- Population sampling and exact shared realization; Event/Lifecycle and their
  reducers/selections; Entity Candidate and source-native Driver Candidate.
- Normal Ibis parameter/hook behavior, concrete temporary tables/views, memtables,
  UDF registration and Driver numerical macros. Required validations, exact
  semantics, atomic primary/parts/Evidence publication and cold reads remain.
- Owned cancellation, connection closure and local publication recovery remain.
  Discharging publication ownership does not certify deletion of unknown objects.

## One operator abstraction

The existing method registry owns pure backend capability declarations, including
retained import. Runtime resolves declared backends to concrete factories; it
cannot activate a method absent from the method registration. Keeping factories
in Runtime preserves the compiler's prohibition on importing materialization.

Generic admission no longer checks for a DuckDB backend name, installs Driver
macros or applies DuckDB sampling limits before backend selection. The selected
adapter owns physical eligibility, sampling SQL and numerical preparation.
Generic sampling owns execution order and typed realization validation. No
parallel capability system, generic SQL guard or implicit alternate route exists.

Only the existing DuckDB implementation is registered. Remote backends must prove
compatible operations under read-only accounts and equivalent operator semantics
in their own enabling slices. DuckDB internal temporary resources are permitted
implementation details, not operator-level semantic exceptions.

## Validation

- Adapter/dispatch/JSON/datasource profile/validation focused tests: 124 passed.
- Registry, authority and packaged-skill focused tests: 46 passed.
- Touched-module typing and import contracts passed after separating pure
  declarations from Runtime factories.
- Restored Runtime selection: 145 passed initially, with three stale assertions
  repaired in the follow-up below.
- Follow-up Event/object Candidate/Driver/registry/Lifecycle review Runtime:
  54 passed in 25.94s, including all three prior failures and restored object
  continuations. SDK stubs were used; no external object service was started.
- Final broader restoration Runtime: 157 passed in 59.21s.
- Site content verification: 343 required files verified.
- Final `make check-agent`: lint/import contracts, typing of 334 source modules,
  4922 default tests in 98.45s and API documentation build passed.
- Final site build: Sphinx, Astro check, 321 pages and install-script verification
  passed (`/tmp/marivo-restoration-site-build.log`).
- Final whitespace check passed.

Restored tests retain current Slice 1b/1c contracts: original exceptions propagate,
local Parquet rows count as transferred output, cold statistics use the current
schema, and backend capability registration replaces old engine-version gates.
Tests asserting actual storage integrity errors retain their typed error checks.
No blanket skip/xfail was introduced to make unavailable functionality appear green.

## Independent review

An independent subagent identified and reviewed four boundary corrections:
backend factory binding, retained-import capability, Driver preparation and
sampling physical policy/SQL. It also identified stale version-gate tests, which
were replaced with capability-based rejection or real restored success coverage.
Final independent re-review reported no remaining implementation or documentation
findings. Three conflicting legacy spec statements were corrected. The reviewer
verified the pure declaration / Runtime factory split without executing tests;
all test results here were produced by the implementing agent.


## Reproduction and final candidate

```sh
make test TESTS='tests/test_lazy_duckdb_execution_adapter.py tests/test_lazy_backend_dispatch.py tests/test_lazy_dispatch_authority.py tests/test_datasource_json_source.py tests/test_datasource_profiles_backends.py tests/test_lazy_validation_batches.py'
make runtime-test TESTS='tests/test_lazy_backend_dispatch.py tests/test_lazy_population_sampling.py tests/test_lazy_event_runtime.py tests/test_lazy_event_reducer_runtime.py tests/test_lazy_lifecycle_reducers_runtime.py tests/test_lazy_entity_candidate_publication.py tests/test_lazy_driver_runtime.py tests/test_lazy_driver_object.py tests/test_lazy_materialization_execution.py tests/test_lazy_binding_cold_acceptance.py'
make check-agent
npm --prefix site run verify:content
npm --prefix site run build
```

Final executable manifest from
`tests.test_lazy_adapter_runtime_acceptance._manifest`: 752 files,
SHA-256 `6bf2f94172189d6019fd21f488983397cb02172ae77594db7feee71880fabe65`.
The full Runtime/release suite and live object connector gate were not run.
