# Marivo Executable Semantic and Ontology Extension Design

Status: split into two focused specifications

Date: 2026-07-13

This design has been split into two independent specifications, matching the
two independent release units it originally defined. This page is retained as a
pointer so existing links keep resolving; the normative content now lives in the
two documents below.

- [`Marivo Event and Lifecycle Semantic and Analysis Design`](2026-07-13-event-semantic-and-analysis-design.md)
  — executable `Event`, `StateModel`, and `StateProjection` semantic objects;
  canonical journey/lifecycle materialization; typed reducers, SubjectSet,
  funnel attribution; and their Evidence Engine observations.
  Fully usable without any ontology.
- [`Marivo Ontology Extension Design`](2026-07-13-ontology-extension-design.md)
  — the optional `marivo.ontology` knowledge extension: `SemanticEdge`
  authoring, the read-only semantic index, and the narrow
  `discover.semantic_hypotheses` candidate bridge into typed analysis. Depends
  on, and ships after, the event/lifecycle design.

The two designs share the same non-negotiable boundary: `marivo.semantic`
remains the sole authority for executable business meaning, and the optional
ontology extension may reference typed semantic identities but can never define
executable identity, joins, filters, populations, readiness, or SQL.

The original combined text is preserved in version control history.
