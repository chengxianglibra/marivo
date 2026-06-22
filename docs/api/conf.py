"""Sphinx configuration for the Marivo Python API reference.

Builds an HTML reference for the public Python surface
(``marivo.datasource``, ``marivo.semantic``, ``marivo.analysis``) from each
module's ``__all__`` exports and Google-style docstrings. The output is
published as a static subtree at ``/api/`` by the Astro site.
"""

from importlib.metadata import version as _metadata_version

project = "Marivo"
author = "Marivo contributors"
copyright = "2026, Marivo contributors"
release = _metadata_version("marivo")
version = release

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx.ext.intersphinx",
]

# Document the public surface re-exported through each package ``__all__``.
# A custom autosummary class template (``_templates/autosummary/class.rst``)
# renders each class with ``:members:`` and omits the noisy ``__init__`` rubric
# emitted by the default template.
templates_path = ["_templates"]
autosummary_generate = True
autodoc_member_order = "bysource"
autodoc_typehints = "description"
autodoc_default_options = {
    "members": True,
    "show-inheritance": True,
}

# Docstrings across the public surface are Google-style.
napoleon_google_docstring = True
napoleon_numpy_docstring = False

# Only map external inventories that publish a stable ``objects.inv`` so the
# strict ``-W`` build does not fail on unresolved cross-references.
intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
    "pandas": ("https://pandas.pydata.org/docs", None),
}
suppress_warnings = ["intersphinx.inventory"]

html_theme = "pydata_sphinx_theme"
html_title = f"Marivo {release} API"
html_theme_options = {
    "github_url": "https://github.com/chengxianglibra/marivo",
}
