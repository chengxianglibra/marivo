# Private native Dataset disclosure inputs

## Boundary

This owner follow-up addresses the missing native capability/Help inputs reported
by `/Users/lichengxiang/.codex/marivo-slice-8a-9gayswve/README.md`. It is a private
prerequisite repair, not Slice 8a completion or public activation. The original
frozen baseline and candidate are not changed.

The current public surface remains the eager 82 exports, 128 capabilities and
169 Help targets. No public Help coordinator, export list, Session dispatch,
Store-generation selector, site page or packaged workflow skill is activated.
No commit, push, package installation or release is included.

## Inputs and ownership

`marivo.analysis._capabilities.dataset_registry.prepare()` explicitly assembles
five private owner providers: Dataset Core, Observation, Typed Operators,
Event/Lifecycle, and Session/retained Runtime reads. It returns native descriptors
bound to the actual family registry and implementation objects. There is no
process-global target installation, environment switch or renderer inventory.
Observation receives the actual source receiver from the assembly layer, preserving
its prohibition on importing Session or executing analysis implementations.

The prepared export bindings cover the accepted 100-symbol manifest. Each
callable supplies reflected bindings, parameter acquisition, state/output facts,
constraints, effects, failures/repairs and an executable example. Shared Dataset
methods have one canonical leaf across concrete receivers. Metric and funnel
attribution retain separate registered admissions, including exact-instance
resolution of the shared Python method.

All nine paired families supply their actual registered shapes and the complete
fields of their 18 concrete row-semantics classes. Core sealed descriptor and
temporal value variants are also registered explicitly. Variant fields reflect
the stored dataclass contract; implementation convenience properties do not
become additional stored fields. Public type fields and methods retain their
separate reflection checks.

Static type pages name exact acquisition paths and producer/consumer routes.
Current continuations still come exclusively from existing consumer admission;
no static descriptor controls execution or `DatasetContract`.

The 21 retained catalog read inputs already existed natively. The prepared
registry exposes `retained_catalog_inputs` as references to those original
owner descriptors, with independent identity and inventory checks. They are not
copied into the Dataset vocabulary. Their existing Help and semantic tests remain
the owning evidence; their final integration belongs to Slice 8a.

## Canonical Help and private receivers

Population, Event and Lifecycle pairs resolve to `analysis.population`,
`analysis.event_dataset` and `analysis.lifecycle_dataset`. The conflicting old
Module 6 routes are corrected without compatibility aliases. The six analysis
hubs remain bounded; every prepared discovery member has one parent and resolves
independently through the neutral resolver. Rendering uses the existing analysis
budgets and fails on overflow rather than truncating missing content.

The private `PreparedSession` composes `LazySources` and `DatasetRuntime`.
`PreparedSessionNamespace` binds an explicit project and already loaded semantic
Registry/sidecar. It delegates real creation/recovery and retained reads; it does
not use eager Session implementations or signature-only stubs. Its private
bootstrap intentionally receives prepared semantic inputs. The final public
Session loader, backend wiring and public facade signatures are still Slice 8a
assembly work; these inputs do not prove that today's eager `Session` binding is
ready to publish.

Selected abandonment reuses the existing writer guard, recovery snapshot,
resource discharge and terminal/fencing protocol. The reconciliation owner now
accepts an optional exact Run selection; normal whole-Session recovery keeps its
previous behavior. A repeated request for one failed Run cannot reconcile another
incomplete Run, and committed success cannot be abandoned.

## Verification and reproduction

The independent tests freeze the accepted export names, required focused targets,
exact family shapes, complete variant field names and critical parameter/default
contracts. They also exercise missing/duplicate owners, parameter/signature/type/
variant drift, missing consumer links, dangling routes, budget overflow, source-free
preparation, type/instance/module resolution, and no public activation.

Every new focused callable example has a test-owned execution case. Pure examples
use the existing no-I/O source fixtures; Runtime examples use real DuckDB input,
Dataset execution, committed Delta Findings, v3 reads and guarded recovery.
Example bodies come from production providers and are executed without rewriting.

Run the focused gates with:

```sh
make test TESTS='tests/test_lazy_disclosure.py tests/test_lazy_disclosure_examples.py tests/test_lazy_reconciliation_snapshot.py tests/test_lazy_materialization_guard.py'
make runtime-test TESTS='tests/test_lazy_disclosure_examples.py tests/test_lazy_reconciliation_snapshot.py tests/test_lazy_materialization_guard.py'
make check-agent
.venv/bin/python scripts/inspect_private_disclosure.py --output evidence/native-disclosure-20260911
```

The inspection command writes complete native inputs, rendered pages and a
per-file SHA-256 manifest covering repository Python code, tests, scripts and
build/test configuration, including these uncommitted additions. Logs and their
checksums are recorded in the same task-specific evidence directory. Existing
unrelated `evidence/` contents are preserved.

## Observed prerequisite acceptance (2026-09-11)

The missing native-input prerequisite was closed on the initial accepted working tree.
This does not close Slice 8a or any public/persistence activation gate.

- Prepared inputs cover 100 exports, 191 private Help targets, 100 callable
  descriptors and 66 type/family descriptors. The nine families cover 18 stored
  row-semantics variants. The 21 existing catalog inputs remain owner references.
- All 80 pure examples and 20 Runtime examples execute provider-owned bodies.
  After execution, the Runtime example suite moves the source database offline
  before retained reads, Session inspection and recovery. It also verifies exact
  bound-method resolution on a recovered Materialized Dataset.
- Focused regression gate: 118 passed in 6.68 seconds.
- Targeted Runtime gate: one integrated test passed in 16.05 seconds, executing
  all 20 Runtime examples.
- `make check-agent`: formatting, lint and import contracts passed; typing passed
  for 482 source files; 7,191 tests passed in 108.78 seconds; API documentation built.
- Public 82-export, 128-capability and 169-Help snapshots remain unchanged.

The 999-file executable candidate has SHA-256
`92054bcaec47eb5eeabfe5b2bb83fa8b85e87f89b09d5e5c9e81654a994c9a7f`.
The full executable manifest and native-input capture match byte-for-byte before
and after the final gates. The fingerprint includes the new source, tests and
inspection script; documentation and generated evidence are excluded.

Exact commands, outcomes and log/input checksums are in
[`validation.json`](../../../evidence/native-disclosure-20260911/validation.json).
The same directory contains `native-inputs.json`, `executable-manifest.json`,
`focused.log`, `runtime.log` and `check-agent.log`. The old frozen inspection
candidate and unrelated evidence remain preserved. No changes are committed.

## Review disposition and follow-up (2026-09-11)

The supplied Standards and Spec findings were checked against the implementation,
the neutral resolver, execution registrations, Session identity contract and tests.
This follow-up preserves the initial evidence above; its candidate and logs live
under `evidence/native-disclosure-20260911/review-fix/`.

| Suggestion | Disposition and evidence |
| --- | --- |
| Bare `KeyError` mixed with structured errors | The proposed hard violation is not substantiated: `try_resolve_live_string_target`, `_resolve_callable` and `_resolve_type` in the shared resolver explicitly catch `KeyError` for lookup misses. Keep that protocol, document the local reason and adapt final private `resolve()` failures to structured `DatasetRegistrationError`, with repair routes read from the actual root. Missing export links now also fail structurally during assembly. |
| Possible missing capability test coverage | Not substantiated. `test_lazy_disclosure.py` independently freezes manifests, routes, fields, shapes, budgets and reachability; the example suite executes provider-owned code. A codegraph coverage label does not override those executable cases. |
| Open `state: str` | Adopted: the Core helper now accepts only `Literal["both", "logical", "materialized"]`. |
| Duplicate sealed-variant attachment | Adopted: one typed `with_sealed_variants` helper attaches declared variants and rejects missing type owners; variant declarations remain beside their owners. |
| Duplicate filter and namespace member inventories | Adopted: callable inputs carry owner-authored `discovery_group` metadata. Observation assigns it in the predicate construction loop; Session assigns it from the actual namespace receiver. Navigation consumes that metadata without per-callable lists. Independent fixed membership tests cover all twelve filters and six namespace methods. |
| Hardcoded funnel resolution and private receiver traversal | Adopted: candidate shape scopes come from the assembly's execution registry via registration ids. Bound methods select a unique most-specific scope; equal or incomparable scopes fail as ambiguous. The operator provider explicitly owns the unbound default. Tests rename targets and reverse descriptor order, proving selection does not depend on canonical-id spelling or order; missing/duplicate defaults fail assembly. Dynamic continuation admission is unchanged. |
| Exact reconciliation selection silently misses | Adopted: reconciliation itself rejects missing, foreign and successful Runs before any resource discharge. The facade only delegates under the writer guard. An already failed Run without remaining obligations remains a valid idempotent result. Direct owner tests verify all four cases, unchanged database contents and no external cleanup. |
| `PreparedSession` as an unnecessary facade | Not substantiated: the accepted plan explicitly requires executable thin private receivers bound to existing source and v3 Runtime owners. Public loader/backend integration remains deferred. |
| `resume(by=...)` and name/id disambiguation | Retained: the existing Session `resume` contract already accepts exact names/ids and the explicit `by` selector. The private receiver must not silently choose another Session when these identities collide. This preserves identity semantics without using the eager implementation. |
| Split `validate`, replace owner/kind partitioning, rewrite descriptor tuples or rename `P`/`common`/`bind` | Deferred as structural/style judgments without a demonstrated defect. Central topology partitioning remains registry-owned; no renderer inventory exists. A broad rewrite would enlarge this prerequisite repair without adding contract evidence. |

The inspection capture now includes discovery-group and unbound-default facts.
The follow-up preserves all 191 rendered private Help pages, the 100 export
mappings and 21 retained catalog references byte-for-byte against the initial
input capture. The public 82/128/169 snapshots also remain unchanged.

Final follow-up gates on the 999-file executable candidate
`bc8150c9934c8a33c651dcc2c04eab11b39ef3015ee1e640be91a584f87f64f7`:

- Focused regressions: 128 passed in 5.80 seconds.
- Targeted Runtime: one integrated test passed in 4.83 seconds, executing all
  20 provider examples including source-offline retained reads.
- `make check-agent`: formatting, lint, import contracts and typing passed;
  7,201 tests passed in 81.80 seconds; API documentation built.
- The executable and native-input manifests match before and after all gates.

The exact command arguments, exit codes and checksums are preserved in
[`review-fix/validation.json`](../../../evidence/native-disclosure-20260911/review-fix/validation.json).
No commit, push, release or public/persistence activation is included.
