# Skill / Semantic Layer Authoring Overall Design

Status: superseded.

This design has been superseded by
`docs/specs/semantic/authoring-pipeline-design.md`. Keep this file only as a
compatibility pointer for older references.

The old standalone static-check workflow is no longer the public agent
contract. Normal semantic authoring now follows the authoring pipeline design:

1. Discovery and source inspection.
2. `project.assess_authoring(...)` for each candidate object before writing.
3. A single `project.readiness(...)` closeout for the target refs.

The standalone authoring-input checker is an internal implementation detail of
`project.assess_authoring(...)`, not a normal skill call-site. Richness findings
are folded into readiness warnings and `richness_summary`; normal closeout does
not use a separate richness gate.
