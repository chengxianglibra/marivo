marivo.ontology
===============

.. currentmodule:: marivo.ontology

.. automodule:: marivo.ontology
   :no-members:

Ontology is an optional contextual extension over the executable semantic
catalog. It can suggest unscored Metric hypotheses through
``session.discover.semantic_hypotheses(...)``; it cannot define identity,
joins, filters, readiness, SQL, or causal evidence. Use
``marivo.help("ontology.authoring")`` for the live authoring contract.

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
