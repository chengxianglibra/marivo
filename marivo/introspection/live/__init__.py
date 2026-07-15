"""Neutral shared foundation for Marivo's live authoring surfaces.

This subpackage owns the cross-surface primitives (environment fingerprint,
live help targets, surface limits), the directional handoff schemas, the
authoring state/effect/transition/repair types, and the registry/resolver/
renderer/error contracts consumed by the datasource, semantic, and analysis
surfaces. It is private implementation infrastructure: nothing here is added
to a public ``__all__``.

The package is a leaf. It must not import ``marivo.semantic``,
``marivo.datasource``, or ``marivo.analysis`` at module load time so that
importlinter layering (datasource/semantic must not depend on analysis) holds.
"""
