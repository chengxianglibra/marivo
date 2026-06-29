# Skill Examples Real Project Design

Date: 2026-06-29
Status: Approved design, pending written-spec review
Related:
`agent-guide.md`,
`marivo/skills/marivo-semantic/SKILL.md`,
`marivo/skills/marivo-analysis/SKILL.md`,
`scripts/run_skill_examples.py`

## Problem

The packaged `marivo-semantic` and `marivo-analysis` example files currently
mix user-facing agent guidance with test-environment bootstrapping.

For `marivo-semantic`, the public examples create temporary DuckDB projects,
change process working directories, dynamically write semantic files, and build
a whole toy domain in one script. That makes the visible pattern look like
"bootstrap a project and author a full semantic layer" instead of the intended
agent loop:

```text
help -> discover -> settle/grill -> author one object -> verify
```

For `marivo-analysis`, most examples enter through an `ensure_loaded()` helper
that creates a tiny semantic project and attaches a session. That keeps smoke
tests runnable, but it teaches agents to construct semantic infrastructure
before analysis instead of consuming an already-ready semantic catalog.

The main docs already state the correct layering, but examples are high-signal
training material for agents. If example code teaches a different posture than
the prose, agents will copy the code.

## Goals

- Make public skill examples look like real agent usage in a Marivo project.
- Keep executable regression coverage through `make examples-check`.
- Move temporary project, DuckDB seeding, and semantic bootstrap logic behind
  harness/support code rather than showing it as the public pattern.
- Preserve the existing skill layering:
  - `marivo-semantic` owns workflow and routing.
  - `ms.help(...)` owns static authoring contracts.
  - `md.discover_*` owns datasource evidence.
  - `marivo-analysis` owns analysis workflow, session discipline, and closeout.
  - `mv.help(...)`, artifact `.show()`, and artifact `.contract()` own static
    and dynamic analysis guidance.
- Update the example checker so `make examples-check` enforces the new teaching
  contract instead of the old complete-toy-domain contract.

## Non-Goals

- Do not redesign public `marivo.datasource`, `marivo.semantic`, or
  `marivo.analysis` APIs.
- Do not remove executable example coverage.
- Do not rewrite unrelated site documentation.
- Do not add compatibility shims for old example file names if the public
  example contract changes.
- Do not make analysis examples author semantic-layer objects.

## Selected Approach

Use public examples as real-project flows while the example runner supplies a
fixture project behind the scenes.

The public files should be copyable and representative: they should start from
the objects an agent has in a real project, such as datasource refs, catalog
objects, stable analysis sessions, and Marivo artifacts. For executable smoke
coverage, the runner should create a temporary fixture project and run each
public example with that project as the current working directory. Public
examples should not import fixture helpers directly.

This approach preserves the two things we need at the same time:

- examples stay honest as agent-facing guidance;
- `make examples-check` still catches drift in runnable surfaces.

## `marivo-semantic` Example Contract

`marivo/skills/marivo-semantic/references/examples/` should contain only
agent-facing semantic authoring flows.

Recommended public files:

- `01_discover_and_grill.py`
- `02_author_one_object.py`

`01_discover_and_grill.py` should show a real-project discovery pass for one
table or table group:

- read the relevant datasource and authoring help;
- bind datasource and table refs;
- run `md.test(...)` against the existing datasource ref to confirm it is usable;
- run bounded `md.discover_*` calls;
- inspect current semantic catalog state with `ms.load()`;
- stop at one unresolved semantic decision when evidence is not enough.

The grill turn should be represented as a single printed line beginning with
`GRILL:` followed by one unresolved semantic decision, then the script exits
successfully. The runner should enforce this structurally: the discovery/grill
example contains discovery and catalog inspection, but no semantic authoring
constructors, no project file writes, and no `ms.verify_object(...)` after the
grill line.

`02_author_one_object.py` should show a narrow authoring loop:

- assume discovery and user intent have settled one object;
- inspect the relevant `ms.help(...)` topic;
- author or update one normal project semantic declaration;
- run `ms.verify_object(ref)`;
- run `ms.readiness(...)` for the verified ref or handoff refs;
- stop after that object passes and readiness is inspected.

It may show the loop moving from one dependency to the next, but it must not
look like a single script authoring a whole domain before validation.

This intentionally removes the public-example requirement that one file cover
every semantic API family such as `ms.relationship(...)`, cross-entity
`@ms.metric(...)`, `ms.ratio(...)`, `ms.weighted_average(...)`, and
`ms.linear(...)`. Those API contracts belong to `ms.help(...)`, runtime
validation, and API-level tests. Public examples should teach the agent loop,
not act as the only runnable catalogue of every semantic constructor.

`md.register(...)` remains datasource setup and should live in fixture/support
code or datasource-specific docs, not in the public semantic authoring flow.

Visible semantic examples must not contain setup-heavy patterns such as
`tempfile`, `os.chdir`, direct DuckDB data seeding, project bootstrap through
`Path.write_text`, or dynamic construction of an entire semantic model as the
main story.

## `marivo-analysis` Example Contract

`marivo/skills/marivo-analysis/references/examples/` should start from a ready
semantic project and consume catalog-backed metric objects.

Recommended public files:

- `00_real_project_template.py`
- `01_observe_single_window.py`
- `02_compare_yoy.py`
- `03_attribute_attribution.py`
- `04_discover_point_anomaly.py`
- `14_derive_metric_frame.py`
- `99_pitfall_pass_delta_to_compare.py`

The visible flow should use:

- `mv.help(...)` or `mv.help_text(...)` for static contract confirmation;
- `session = mv.session.get_or_create(...)` for stable task state;
- `session.catalog.get(...)` for metrics, dimensions, and time dimensions;
- `artifact.show()` for bounded evidence;
- `artifact.contract()` at deliberate decision points before chaining.

Analysis examples must not import `_fixtures.tiny_semantic`,
`_support.example_project`, or any other harness helper directly. They should
not call `ensure_loaded()` or build semantic definitions. The runner, not the
public example, attaches the fixture project before execution.

`00_real_project_template.py` remains a non-executed template that assumes the
user already has `models/semantic/` definitions. It should be checked for
syntax and required snippets, not executed against the fixture.

`14_derive_metric_frame.py` remains the stable operator-aligned slot for the
governed custom-Ibis re-entry example.

## Support Fixtures

Fixture code should live under internal support directories and be invoked by
`scripts/run_skill_examples.py`, not by public examples.

Semantic support:

- module:
  `marivo/skills/marivo-semantic/references/examples/_support/example_project.py`;
- entrypoint: `semantic_examples_project()`;
- return contract: a context manager whose value exposes `root`, `warehouse_ref`,
  `orders_table`, optional related table refs, and any pre-authored refs needed
  by the author-one-object example;
- responsibility: create the temporary DuckDB-backed project, seed data, write
  any prerequisite datasource or baseline semantic files, and make the project
  current for the example run.

Analysis support:

- module:
  `marivo/skills/marivo-analysis/references/examples/_support/example_project.py`;
- entrypoint: `analysis_examples_project()`;
- return contract: a context manager whose value exposes `root`, `session_name`,
  `metric_id`, `derived_metric_id`, and stable dimension/time-dimension ids;
- responsibility: create the ready semantic project and runtime backend that
  analysis examples consume.

Support code purpose must be explicit:

- it exists for `make examples-check` and local smoke execution;
- it may create a temporary project, seed DuckDB, and load semantic objects;
- public examples should not expose that bootstrapping as the pattern to copy;
- support helpers should return real project-facing objects, such as a current
  working directory, session, catalog, refs, or metric ids.

The runner should reject public examples that import `_fixtures`,
`_support.example_project`, or `_support` directly. It should allow those module
paths only inside support files or inside the runner itself.

## Runner And Test Changes

Update `scripts/run_skill_examples.py` so it enforces the new teaching
contract.

Runner changes:

- keep executing all non-template examples through `make examples-check`;
- continue syntax-checking template files;
- change semantic example checks from "complete model contains every semantic
  category" to "workflow examples contain help, discovery, catalog inspection,
  one-object authoring, verification, and readiness";
- reject public example setup smells such as `tempfile`, `os.chdir`, direct
  DuckDB seeding, `Path.write_text` bootstrap code, direct `_fixtures` imports,
  direct `_support` imports, and `ensure_loaded()`;
- preserve the existing forbidden stale semantic references:
  `md.inspect_columns`, `md.inspect_table`, `md.probe_join_keys`,
  `project.assess_authoring(`, `ms.AuthoringSourceInput(`, and
  `judgment_targets`;
- keep pitfall keyword checks for `99_pitfall_*.py`.

Testing boundary:

- `make examples-check` is the only check that should execute example scripts
  and validate the current public example set.
- Pytest should not duplicate example execution through `runpy`, subprocesses,
  or file-list assertions whose only effect is confirming the current skill
  state.
- Keep pytest coverage only for reusable runner behavior that has real logic
  independent of today's example contents, such as template detection, pitfall
  keyword extraction, timeout/error reporting, or helper functions whose
  behavior could regress without an example changing.
- Delete or shrink tests that merely assert exact example names, exact snippets,
  or that all examples execute. Those are `examples-check` responsibilities.
- If a public-example smell check is useful, implement it in
  `scripts/run_skill_examples.py` so `make examples-check` owns it, not as a
  separate pytest snapshot of the same files.
- Rename/delete public example files, update runner constants, and update the
  associated checker expectations atomically in one implementation change.

## Documentation Updates

Update only the skill docs and nearby references that describe example purpose.

The docs should say:

- public examples are agent-facing flows;
- exact callable contracts still come from runtime help;
- setup-heavy code belongs to support fixtures and runner tests;
- analysis examples consume semantic projects and must route missing semantic
  objects back to `marivo-semantic`.

Do not duplicate constructor parameter tables, discovery result schemas, or
analysis operator signatures in the examples or skill prose.

## Verification

Use the narrow checks first:

```bash
make examples-check
make test TESTS='tests/test_run_skill_examples.py'
```

Run additional tests only when the implementation changes runtime behavior or
non-runner code. Run `make lint` if the implementation changes linted Python
or markdown surfaces beyond examples and runner tests.

## Success Criteria

- Public semantic examples no longer show temporary project setup as the agent
  authoring pattern.
- Public semantic examples demonstrate discovery, exactly one printed grill
  decision in the grill example, one-object authoring, `ms.verify_object(ref)`,
  and `ms.readiness(...)`.
- Public analysis examples no longer directly import tiny semantic fixtures or
  call `ensure_loaded()`.
- Analysis examples begin from help, session, catalog objects, artifacts,
  `.show()`, and `.contract()`.
- `make examples-check` still executes all non-template examples.
- Pytest does not duplicate `examples-check` or lock in the current example
  file list as a separate state-confirmation test.
- Unrelated worktree changes remain untouched.
