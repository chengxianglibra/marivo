# Skill / Semantic Layer Authoring Overall Design

> **Superseded for agent authoring:** use
> `docs/specs/semantic/stepwise-authoring-design.md` for the active
> stepwise prepare/verify/readiness workflow. This document remains only as
> historical context for the previous pipeline.

Status: superseded.

This design has been superseded by
`docs/specs/semantic/stepwise-authoring-design.md`. Keep this file only as a
compatibility pointer for older references.

The old standalone static-check workflow is no longer the public agent
contract. Normal semantic authoring now follows the stepwise authoring design:

1. Discovery and source inspection.
2. `project.prepare_entity(...)` / `project.prepare_metric(...)` for each candidate object.
3. `project.verify_object(...)` after writing.
4. A single `project.readiness(...)` closeout for the target refs.

The standalone authoring-input checker is an internal implementation detail of
`project.assess_authoring(...)`, not a normal skill call-site. Richness findings
are folded into readiness warnings and `richness_summary`; normal closeout does
not use a separate richness gate.
