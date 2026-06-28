# Marivo Analysis Skill Layering Design

## Context

`agent-guide.md` defines the analysis guidance boundary:

- `marivo-analysis` owns workflow only: intent routing, session discipline,
  observation points, recovery discipline, and final report shape.
- `mv.help` owns the static analysis contract: signatures, artifact families,
  constraints, return types, errors, and runnable examples.
- Frames and results own dynamic guidance: `show()` describes current state,
  `contract()` describes mechanically valid next actions, and agent judgment
  remains outside Marivo.

The current `marivo-analysis` skill still duplicates API detail, embeds many
operator examples, and points agents to example files as the primary way to
learn analysis flow. That makes the skill heavier than its intended layer.

## Goal

Refactor the packaged `marivo-analysis` skill so agents get a concise,
workflow-first analysis guide that delegates API details to runtime help and
current-state decisions to frames/results.

## Non-Goals

- Do not add a hard example-count rule to `scripts/run_skill_examples.py`.
- Do not redesign the public `marivo.analysis` API.
- Do not move final-report, pitfall, or help contract ownership into the skill.
- Do not author semantic-layer objects as part of the analysis skill workflow.

## Skill Content Shape

Rewrite `marivo/skills/marivo-analysis/SKILL.md` into a short guide with these
sections:

1. **Scope and ownership**
   Explain that the skill owns workflow only. Direct agents to `mv.help()` and
   `mv.help("<topic>")` for signatures, parameters, constraints, return types,
   errors, and examples. Direct them to `artifact.show()` and
   `artifact.contract()` for current artifact state and mechanical next actions.

2. **Start flow**
   Instruct agents to verify the environment, load or inspect the semantic
   catalog, confirm metric ids, create or reuse one stable analysis session,
   and consult `mv.help("agent_surface")` plus the specific topic before using
   an operator.

3. **Analysis loop**
   Keep intent routing as a decision map, not a parameter table. The skill may
   name core paths such as observe, compare, attribute, discover, correlate,
   hypothesis_test, forecast, assess_quality, and derive_metric_frame, but must
   not teach full call contracts. Agents should stop at deliberate observation
   points, read `show()`, then read `contract()` before composing the next step.

4. **Session and recovery discipline**
   Use one session per analysis task. Script splits are allowed when the next
   step depends on observed output, but they are not session boundaries. On
   errors, read structured fields such as code/kind, candidates, and repair
   guidance instead of guessing.

5. **Closeout and recap**
   Final responses must synthesize the answer, evidence, caveats, source
   details, and agent-authored next steps rather than pasting raw `show()`
   output. If the analysis exposes missing semantic-layer objects or metadata
   such as a missing metric, dimension, time dimension, entity relationship,
   unit, or context, the recap should explicitly advise the user to add those
   semantic objects. The analysis skill should not switch into semantic
   authoring; it should route that work to `marivo-semantic`.

## Examples Scope

Prune `marivo/skills/marivo-analysis/references/examples/` to a small set of
necessary runnable templates and smoke examples. The retained files should be
enough to prove the skill package runs, but not enough to serve as the analysis
methodology.

Recommended retained set:

- `00_real_project_template.py`
- `01_observe_single_window.py`
- `02_compare_yoy.py`
- `03_attribute_attribution.py`
- `04_discover_point_anomaly.py`
- `14_derive_metric_frame.py`
- optionally one pitfall example if existing tests or error docs rely on it

Remove specialized transform, panel, segmented, timezone, and many objective
variant examples unless a current test or public error message still requires
one. Do not add a new runner rule that enforces this set.

## Reference Files

Keep reference files only where they own durable guidance:

- `references/final-report.md` remains the detailed final-report contract.
- `references/pitfalls.md` remains the worked error-recovery reference.
- `references/backend-setup.md` remains datasource/runtime setup guidance if
  still current.
- `references/cheatsheet.md` should be reduced or reframed so it points to
  `mv.help` for API contracts rather than duplicating detailed tables.

## Testing And Verification

Use narrow checks first:

- `make test TESTS='tests/test_agent_api_drift.py tests/test_analysis_skill_final_report_guidance.py tests/test_analysis_agent_facing_phase3.py tests/test_run_skill_examples.py'`
- `make examples-check`
- `make lint` if markdown/example changes touch linted surfaces

Because this change prunes examples without adding hard runner enforcement,
tests should verify only the intended public guidance: `show()`/`contract()`,
`mv.help` ownership, absence of removed surfaces, final-report linkage, and
example executability.

## Success Criteria

- `SKILL.md` is materially shorter and workflow-focused.
- Agents are directed to `mv.help` for static contracts and to
  `show()`/`contract()` for dynamic artifact guidance.
- The skill no longer presents examples as the primary analysis methodology.
- The examples directory contains only necessary runnable templates/smokes.
- Final recap guidance tells agents to recommend semantic-layer additions when
  missing semantic objects are exposed during analysis.
- Existing unrelated semantic-layer worktree changes are preserved.
