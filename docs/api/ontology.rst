marivo.ontology
===============

.. currentmodule:: marivo.ontology

.. automodule:: marivo.ontology
   :no-members:

Ontology is an optional contextual extension over the executable semantic
catalog. ``mo.load(semantic=catalog)`` validates authored edges against exact
Semantic refs. Analysis-side hypothesis discovery is not a current public
entrypoint. Artifact association requires the R4/R10 Runtime handoff. Ontology
cannot define identity, joins, filters, readiness, SQL, or causal evidence. Use
``marivo.help("ontology.authoring")`` for the live authoring contract.
``OntologyCatalog.definition_fingerprint`` and
``semantic_catalog_fingerprint`` jointly identify the current contextual
association. An ontology edge does not bind or authorize an Artifact.

Catalog and identity
--------------------

.. autosummary::
   :toctree: api/
   :nosignatures:

   OntologyCatalog
   SemanticEdgeRef

Authoring and loading
---------------------

.. autosummary::
   :toctree: api/
   :nosignatures:

   influences
   related_to
   load

Submodules
----------

.. list-table::
   :widths: 30 70
   :header-rows: 0

   * - ``marivo.ontology.errors``
     - Typed ontology authoring, loading, and help errors.
