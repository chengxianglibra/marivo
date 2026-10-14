"""Private, dependency-neutral definition graph shared by semantic and analysis.

Records here are immutable, process-local values: exact semantic references,
closed Row/Metric/Population/Query variants, resolved operator policies, and
rule versions. They never hold Catalog, Session, Ibis, or Artifact objects and
have no persistence codec. Import the owning submodule directly.
"""
