"""Ontology Help discloses the current authoring and inspection boundaries."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

import marivo.ontology as mo
from marivo._help.render import render_help_text
from tests.shared_fixtures import bootstrap_sales_project_from_template


@pytest.mark.parametrize("target", ("influences", "related_to"))
def test_ontology_help_examples_load_the_authored_declaration(
    target: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bootstrap_sales_project_from_template(tmp_path, with_time=False)
    model = tmp_path / "models" / "semantic" / "sales" / "datasets.py"
    model.write_text(
        model.read_text()
        + "\n@ms.metric()\ndef refund_rate():\n    return revenue\n"
        + "\n@ms.metric()\ndef healthy_order_rate():\n    return revenue\n"
        + "\n@ms.metric()\ndef support_ticket_rate():\n    return revenue\n"
    )
    monkeypatch.chdir(tmp_path)

    def example_source(help_target: str) -> str:
        text = render_help_text(help_target)[0]
        imports = text.split("  Python imports:\n", 1)[1].split("\n\n", 1)[0]
        example = text.split("  Example:\n", 1)[1].split("\n  Constraints:", 1)[0]
        return dedent(imports) + "\n" + dedent(example)

    (tmp_path / "models" / "ontology.py").write_text(example_source(f"ontology.{target}"))
    namespace: dict[str, object] = {}
    exec(
        compile(example_source("ontology.authoring"), "<ontology.authoring example>", "exec"),
        namespace,
    )
    ontology = namespace["ontology"]
    assert isinstance(ontology, mo.OntologyCatalog)
    assert ontology.configured
    assert ontology.edge_count == 1
