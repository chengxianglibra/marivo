"""Namespaced attribution hierarchy layout column constants.

Multi-axis hierarchy emits one row per prefix level with these layout columns.
They are namespaced with an ``attribution_`` prefix so a legal business
dimension literally named ``level``/``axis``/``driver``/``path`` (org tier,
log level, URL path, channel tier) is never mistaken for the internal
hierarchy layout marker (issues #43/#44).

This module is intentionally dependency-free so it can be imported from
``analysis.evidence`` without pulling in session/calendar internals that
would violate the analysis-evidence isolation contract.
"""

from __future__ import annotations

ATTRIBUTION_LEVEL_COLUMN = "attribution_level"
ATTRIBUTION_AXIS_COLUMN = "attribution_axis"
ATTRIBUTION_DRIVER_COLUMN = "attribution_driver"
ATTRIBUTION_PATH_COLUMN = "attribution_path"
