# Symbolic authoring implementation progress

Implementation started on 2026-10-22 at `202b92421571361e4c41edeb4a7ccff7dec09d53`.
The two existing untracked proposal/plan documents are the authorized specification.
No unrelated source edits were present. The implementation request did not authorize
a commit, push, installed-package acceptance or release work. The user subsequently
authorized a scoped local commit with attribution, preserving unrelated work and
explicitly forbidding a push.

Local implementation and acceptance are complete. Two formal implementation
review rounds are closed with no remaining P2+. The later untracked
`docs/architecture/temporal-timezone-filtering-proposal.md` belongs to other work
and was left untouched.

## Ownership and dependencies

| Stage | Owner | Dependencies | Status |
| --- | --- | --- | --- |
| S0 | Lead | Current contracts, proposal D01–D15, plan A01–A22 | Read; baseline recorded |
| S1/S2/S4 | symbolic_core | S0; shared native selector protocol | Implemented; capture and loader regressions repaired |
| S1b | symbolic_boundaries | S1 selector protocol | Implemented; datasource, temporal and ontology consumers migrated |
| S3 / Analysis adapters | symbolic_population | S1 parameter protocol, closed Entity/Relationship adapters | Implemented; shared builders and existing consumers migrated |
| S5/S6 integration | Lead and module owners | S1–S4 | Implemented |
| Existing fixtures, Help, current docs/latest EN-ZH | Lead | Final public contract | Aligned |
| S7 acceptance and independent review | Lead; independent agents after development freeze | Integrated source | Complete; two formal rounds, no remaining P2+, final component checks passed |
| AGENTS.md / packaged skills | Lead | Explicit authorization for named files | User approved all three named files; related clauses aligned |

Shared integration files are owned by the lead. Workers preserve one another's edits.
Internal Ref comparison, hashing, wire identity and stored closed computations remain
owned by their existing implementations. The implementation adds no public binding
step, compatibility finalize entry point, or Store format for authoring symbols.

## Acceptance and evidence

These are local checks against the implemented contract. Test paths below are
relative to `tests/`; the full default gate includes these default tests.

| Acceptance | Implemented behavior and representative evidence |
| --- | --- |
| A01 | Ten native factories preserve private Ref identity/wire while public comparisons build recipes: `semantic/test_symbolic_authoring.py::test_native_factories_preserve_private_identity_and_expression_protocol`, `typing/ref_contract.py` |
| A02 | Module-level and decorator compositions share closed definitions and alias publication: `semantic/test_symbolic_authoring.py::test_direct_and_symbolic_named_metric_dependencies_bind_to_one_definition`, `semantic/test_deferred_authoring.py` |
| A03 | Late default time, permission coordinates and restricted-Ibis forward dependencies: corresponding `constructor_snapshots_*` cases in `semantic/test_semantic_expressions.py` |
| A04 | Exact forward field, Measure, Metric and relationship lookups; missing/wrong-kind refusal: `semantic/test_symbolic_authoring.py` forward and complete-lookup-graph cases |
| A05 | Entity-qualified named Measures and effective relationship roots; invalid names/conflicts: `semantic/test_symbolic_authoring.py::test_measure_identity_uses_effective_relationship_root_and_entity_qualified_names`, existing authoring/loader tests |
| A06 | Stable self/indirect cycle chains and complete lookup before binding: complete-graph and cross-declaration-cycle cases in `semantic/test_symbolic_authoring.py`, existing loader diagnostics |
| A07 | Unit, additivity, fixed-time and foreign-axis contracts retained: `semantic/test_semantic_expressions.py`, `semantic/test_metric_additivity.py`, malformed-policy parity in `semantic/test_symbolic_authoring.py` |
| A08 | Shared pure Population builders, complete/null tuples, sets/projection/product, identity/classify/image/preimage, predicate/count/per and capture ownership: `semantic/test_symbolic_population.py`, `analysis/graph/test_frame_population_construction.py`, existing membership/observation tests |
| A09 | Immutable captured templates; no author callback/global reread, import replay or partial publication: template-independence case in `semantic/test_symbolic_authoring.py`, import-once and loader failure tests |
| A10 | A/B template isolation and preserved older Catalogs: template-independence case, `semantic/test_semantic_expressions.py::test_incomplete_scope_can_be_repaired_without_mutating_old_catalog` |
| A11 | Closed computation identity follows consumed facts: `semantic/test_authoring_exact_inputs.py`, definition identity tests and consumed-default-time/additivity-axis cases in `semantic/test_semantic_expressions.py` |
| A12 | Previously captured closed leaves retain their own facts when published into a changed scope: old-closed-leaf case in `semantic/test_symbolic_authoring.py`, updated-root stored-input case in `semantic/test_semantic_expressions.py` |
| A13 | Source-free construction/loading, closed Session inputs and existing scope/ownership refusal: `semantic/test_semantic_expressions.py`, `semantic/test_semantic_loader.py`, `analysis/session/test_analysis_session_core.py`, `analysis/session/test_observation_binding.py` |
| A14 | Independent fixed-data expected values for filters, default time, derived Measures, ratios, samples, null/empty: existing semantic numeric cases, `analysis/numeric/test_analysis_observation.py` and `analysis/temporal/test_analysis_temporal.py`; these assert numerical expectations, rather than only old/new equality |
| A15 | Fixed/cold distribution and multiroot recovery, separate-process temporal reads: `analysis/materialization/test_distribution_recovery.py`, `test_multiroot_recovery.py`, `analysis/temporal/test_analysis_temporal.py`; no symbolic Store format introduced |
| A16 | Removed public Ref/finalize and retained one ref entry; factory docstrings/roles/routes, Help budgets, CLI, exports and EN/ZH latest aligned: `surface/`, `semantic/test_semantic_live_registry.py`, API build and site content check |
| A17 | Native selector metadata/check/datasource inputs enforce kind and scope: `semantic/test_symbolic_boundaries.py`, existing Catalog/readiness/source-health/datasource suites |
| A18 | Metadata selector roundtrips use private identity facts for comparison/display and Analysis adapters: boundary navigation tests, graph/session suites, `typing/ref_contract.py::_native_selector_roundtrip` |
| A19 | Ordered typed Entity/value pairs retain duplicate, exact-coverage and single-Entity rules: `semantic/test_symbolic_boundaries.py::test_preview_pairs_validate_keys_duplicates_and_exact_coverage`, scoped-preview/source-health tests |
| A20 | Existing Ref payloads, temporal Pydantic wire keys and CLI encoding preserved; import boundaries checked: native identity and temporal wire cases, `surface/test_datasource_imports.py`, `surface/test_semantic_imports.py`, import-linter |
| A21 | Capture rejects complete invalid recipes, receiver/parameter roles, duplicate arguments and malformed top-level Ibis constructors before subsequent model statements: `semantic/test_symbolic_authoring.py`, `semantic/test_symbolic_population.py`; unconsumed Python attributes/formatted strings retain the approved boundary |
| A22 | Ten nominal lookup roles and their exact input/output signatures: `typing/ref_contract.py`, focused runtime kind refusals; pure `Catalog.describe` accepts the finite `LookupSymbol` union and statically rejects ordinary/composite Deferred |

### Final checks

- Unnarrowed `make check-agent` against the final frozen source: formatting of
  805 Python files, lint/import contracts, typing of 330 source files and all
  5,302 default tests passed (281.27s; 781 warnings). Its API documentation stage
  failed on a short RST title underline; the corrected documentation passed
  `make docs-api-agent` with exit 0. Thus the final component checks passed;
  the original combined command itself is not recorded as exit 0.
- A redundant full rerun was stopped at the user's request after the
  documentation-only repair. The user explicitly confirmed that this repair
  needs only relevant verification; no source changed after the passing tests.
- `npm run verify:content` in `site/`: passed, 363 required site files verified.
- Final plan/proposal/ledger checks: 49 relative links, 3 Python snippets, fence
  pairing and whitespace passed. `git diff --check` passed.
- Final source/packaged-skill manifest: all 45 file hashes match the independently
  reviewed snapshot; HEAD remains `202b92421571361e4c41edeb4a7ccff7dec09d53`.
- Local fixed-data numerical and cold-recovery checks are separate evidence:
  source-free replay alone does not establish numerical correctness.
- Provider Runtime, installed-package acceptance and release checks were not run.
  These checks preceded the separately authorized local commit. No push is authorized.

## Review and failures

Formal implementation review R1 used frozen source manifest
`ab555c0a06bc92945786cea4a581893a322e1576da6f301524b89ca7a90528ed`
(45 files). Three agents reviewed other owners' implementations read-only.
Historical proposal review is not implementation evidence.
Local, Runtime, installed-package and release evidence will be reported separately.

| Finding | Review evidence | Repair and verification | Status |
| --- | --- | --- | --- |
| R1-F1 / P2: incomplete capture role/shape/duplicate-argument validation | Core reviewer reproduced literal/composite Population relationship steps; binding reviewer reproduced wrong via/time/per roles and duplicate round/clip arguments, including a public project that loaded | Existing shared capture validator checks known roles, exact path carriers and duplicate assignments; Population source tests use before/after-capture sentinels. R2 additionally found malformed top-level `ibis.cases`; its constructor signatures and pair shapes now share capture validation | R2 independent core reviewer confirmed closed after targeted repair; 94 cases passed |
| R1-F2 / P2: malformed symbolic Measure additivity silently inherited | Function/string/object additivity accepted only on symbolic path | Exact admitted policy validation precedes both branches; symbolic/closed malformed-policy parity tests | R2 independently confirmed closed |
| R1-F3 / P2: missing public factory docstrings | All ten native factory methods lacked documentation | Purpose, Args, Returns, Example and Constraints documented per kind | R2 independently confirmed closed |
| R1-F4 / P2: factory Help omitted semantic roles and owner routes | Parameter-only identity, Entity count and relationship-path roles were undisclosed | Per-kind role text and native owner links added without relaxing Help budgets; representative public Help role regressions added | R2 independently confirmed closed |
| R2-F5 / P2: nominal pure-identity signature too broad | Lead and independent Population reviewer reproduced static acceptance of ordinary/composite Deferred by `Catalog.describe` | `LookupSymbol` is the finite union of ten NewTypes; metadata outputs and pure lookup inputs carry that union, including `symbol_from_ref` and ExpressionContract. Positive roundtrips and exact negative typing assertions added | R2 independently confirmed closed: separate no-ignore mypy probe rejects exactly the two invalid calls, positive selector roundtrips pass; 5 boundary tests and all ten native dispatch roundtrips pass |

R1 public selector/backing/wire reviewer found no additional P2+ in that scope.
R1 repairs passed 185 focused core/Population/semantic/telemetry cases and focused
typing/lint. R2 reviewed all four repairs and adjacent risks. Top-level constructor
and nominal lookup corrections are re-verifications within R2, not additional
formal review rounds.

R2 initially used source aggregate
`a774894f1b1b31b7ba97e632f2ec66a8903f93b5de8f68e13bdafe2fca17ae07`.
The repaired capture snapshot was
`673c6a92a041486e110cadcf55e6f0f01695cf3fecacdbf4b49bd8054294a20c`.
The final 45-file source/packaged-skill manifest aggregate is
`168b26d90da1a5505509f55cc12accbfa81a51788cdf60fda6a56d8ab24e36fd`
(`/tmp/marivo-symbolic-review-r2-final.sha256`). No source edits are permitted
during the final gate. Independent reviewer checked all 45 hashes before and after
the final nominal-selector verification. R2 is closed with no remaining P2+.

The first complete final-gate attempt passed lint, imports, typing and all 5,302
default tests, then failed the API documentation stage on one short RST title
underline. The underline was corrected without changing source and the API
documentation build passed separately. A redundant full rerun was terminated
with exit 143 per the user's subsequent instruction, rather than treated as a
new passing gate. Earlier partial integration failures remain development evidence.

### Development checks (not final acceptance)

- Core focused regression: 16 cases passed, including ten factory roles, immediate
  recipe rejection, forward dependency/cycle handling, template A/B isolation,
  effective relationship roots and preservation of captured closed facts.
- Population: 10 new focused cases passed; 88 existing Population/Frame bridge,
  membership and source-fusion cases passed before the final capture integration.
- Boundary: five new selector/pair/wire cases passed; expanded datasource and
  temporal development runs passed 52 and 123 cases respectively (remaining old
  test-protocol expectations were separately identified and migrated).
- Fixed-data numerical and persistence/temporal regression:
  `make test TESTS='tests/analysis/numeric/test_analysis_observation.py tests/analysis/materialization/test_distribution_recovery.py tests/analysis/materialization/test_multiroot_recovery.py tests/analysis/temporal/test_analysis_temporal.py'`
  passed 51 cases. This is local evidence, not provider Runtime or release evidence.
- `npm run verify:content` in `site/` passed after latest EN-ZH authoring migration.
- Integration runs exposed old Ref-based expectations and genuine capture-role
  regressions. Entity count receivers and finite Population required-argument
  checks were repaired; no incomplete integration run is a final passing gate.
- Current consumer discovery additionally found ontology authoring consumes
  semantic lookup identities. Its native-selector adapter belongs to S1b;
  ontology meaning, edge storage and execution are unchanged in scope.

## Approved protected-file alignment

The user explicitly approved the following three named files in this chat before
they were edited. Their related clauses have been aligned.

`AGENTS.md`, Semantic Expressions and Resolution: replace the explicit
finalize/resolve rule with:

> Physical constructors and `ms.ref.*` capture native typed Deferred symbols.
> Compose them directly; the loader binds the complete dependency scope and
> publishes closed Catalog values. Bare lookup symbols are not closed analysis
> inputs. Never mutate resolved values, refresh from live context, or replay
> imports or module effects. Reference-resolving consumers use their completed scope.

`marivo/skills/marivo-semantic/SKILL.md`, Expression authoring boundary: replace
the physical-finalization workflow with direct symbol composition and one complete
load, keeping captured closed facts immutable. Explain native lookup selectors
versus closed `catalog.<collection>.get(path)` inputs, and capture-boundary repair
for unsupported recipes. Keep the existing business authority and source-read rules.

`marivo/skills/marivo-analysis/SKILL.md`, identity lookup/handoff guidance: select
closed inputs through the matching Catalog collection; use lookup symbols only
for explicitly supported metadata/readiness operations. Keep existing analysis
judgment, readiness and execution boundaries.
