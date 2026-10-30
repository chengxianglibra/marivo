"""Ontology Help discloses the current authoring and inspection boundaries."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

import marivo
import marivo.ontology as mo
from marivo._help.render import render_help_text
from marivo.ontology.errors import OntologyLoadError
from tests.shared_fixtures import bootstrap_sales_project_from_template


def test_ontology_load_help_routes_inspection_and_failure_repair(capsys) -> None:
    marivo.help("ontology.authoring")
    output = capsys.readouterr().out
    assert "source module and its imports execute as Python" in output
    assert "not refreshed from disk" in output
    assert "no public analysis-side hypothesis discovery entrypoint" in output
    assert "error.issues" in output
    assert "marivo.help(type(ontology))" in output

    marivo.help(mo.OntologyCatalog)
    output = capsys.readouterr().out
    assert "at most the first 8 edges by name" in output
    assert "guardrails are omitted" in output
    assert "no public edge search, full enumeration, or individual edge read" in output

    marivo.help(OntologyLoadError)
    assert "error.issues" in capsys.readouterr().out


def test_ontology_relation_help_describes_declarations_not_discovery(capsys) -> None:
    marivo.help("ontology.influences")
    output = capsys.readouterr().out
    assert "driver is a hypothesis about the outcome" in output
    assert "Discovery matches" not in output
    assert "native ms.ref lookup symbols" in output
    assert "driver accepts Entity, Measure, or Metric; outcome accepts Entity or Metric" in output

    marivo.help("ontology.related_to")
    output = capsys.readouterr().out
    assert "only one declaration per pair is allowed" in output
    assert "Discovery proposes" not in output
    assert "distinct native ms.ref lookup symbols" in output
    assert "Both endpoints accept Entity, Measure, or Metric" in output

    for target in ("influences", "related_to"):
        text = render_help_text(f"ontology.{target}")[0]
        for following in ("semantic.ref", "semantic.ai_context", "ontology.authoring"):
            assert f'marivo.help("{following}")' in text
            assert render_help_text(following)[0].startswith(f"Target: {following}\n")


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
    assert "metric:sales.refund_rate" in ontology.render()
    assert f": {target} " in ontology.render()
