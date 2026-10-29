"""Sphinx configuration for the Marivo Python API reference.

Builds an HTML reference for the public Python surface
(``marivo.datasource``, ``marivo.semantic``, ``marivo.analysis``,
``marivo.ontology``) from each
module's ``__all__`` exports and Google-style docstrings. The output is
published as a static subtree at ``/api/`` by the Astro site.
"""

from importlib.metadata import version as _metadata_version
from inspect import getattr_static
from typing import get_type_hints

from sphinx.application import Sphinx
from sphinx.ext.autodoc import Options
from sphinx.util.typing import stringify_annotation

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
]

# Document the public surface re-exported through each package ``__all__``.
# A custom autosummary class template (``_templates/autosummary/class.rst``)
# renders each class with ``:members:`` and omits the noisy ``__init__`` rubric
# emitted by the default template.
templates_path = ["_templates"]
autosummary_generate = True
autodoc_member_order = "bysource"
autodoc_typehints = "description"
toc_object_entries_show_parents = "hide"
autodoc_default_options = {
    "members": True,
    "show-inheritance": True,
}

# Docstrings across the public surface are Google-style.
napoleon_google_docstring = True
napoleon_numpy_docstring = False

html_theme = "pydata_sphinx_theme"
html_title = f"Marivo {release} API"
html_theme_options = {
    "github_url": "https://github.com/chengxianglibra/marivo",
    "show_nav_level": 2,
    "show_toc_level": 2,
}


def _returned_type_signature(
    app: Sphinx,
    what: str,
    name: str,
    obj: object,
    options: Options,
    signature: str | None,
    return_annotation: str | None,
) -> tuple[str, str | None] | None:
    """Analysis and Semantic classes describe acquired values, not internal init APIs."""
    if name == "marivo.analysis.ArtifactRef" and isinstance(obj, type):
        # Preserve the initializer parameter type when Sphinx renders this class
        # alone: Pydantic's model field annotation describes the stored value.
        app.env.current_document.autodoc_annotations[name]["ref"] = stringify_annotation(
            get_type_hints(getattr_static(obj, "__init__"))["ref"]
        )
    if (
        what == "class"
        and name.startswith(("marivo.analysis.", "marivo.semantic."))
        and signature is not None
    ):
        return "", None
    return None


def setup(app: Sphinx) -> None:
    app.connect("autodoc-process-signature", _returned_type_signature)
