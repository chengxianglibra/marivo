Marivo Python API Reference
===========================

Marivo's public Python surface has one global help coordinator and three
execution modules. Each module page groups the symbols that module's
``__all__`` exports into thematic sections, generated from the package's
Google-style docstrings. Every symbol links to its own page.

Global help
-----------

Use ``python -m marivo help`` to verify the selected environment, then use
``marivo.help(...)`` for bounded global or focused help.

.. currentmodule:: marivo

.. autosummary::
   :toctree: api/
   :nosignatures:

   help

.. list-table::
   :widths: 25 75
   :header-rows: 0

   * - :doc:`datasource <datasource>`
     - Connect to, inspect, and register data sources.
   * - :doc:`semantic <semantic>`
     - Declare entities, dimensions, measures, and metrics.
   * - :doc:`analysis <analysis>`
     - Run metric-centered analysis over the semantic layer.

.. toctree::
   :maxdepth: 2
   :caption: Modules
   :hidden:

   datasource
   semantic
   analysis

Indices
-------

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`
