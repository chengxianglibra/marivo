"""Independent rejected call shapes at the public typing boundary."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from tests.support.paths import PROJECT_ROOT


def test_public_dsl_rejects_wrong_kinds_and_methods(tmp_path: Path) -> None:
    source = tmp_path / "bad_public_analysis.py"
    program = """
import marivo.analysis as mv
import marivo.semantic as ms
from ibis.common.deferred import Deferred
from marivo.semantic.expressions import Relationship

mv.LogicalNumericRelation  # invalid: retired logical type
mv.MaterializedNumericRelation  # invalid: retired materialized type
mv.mean  # invalid: retired row factory
mv.time_grid  # invalid: retired graph time grid
mv.time_scope  # invalid: retired module-level time constructor
mv.instant  # invalid: Session-only instant constructor
session = mv.session.get_or_create('static', report_timezone='UTC')
population = session.members(ms.ref.entity('sales.customer'))
session.members(ms.ref.metric('sales.revenue'))  # invalid: entity kind
mv.identities(ms.ref.entity('sales.customer'), keys=(('C1',),))  # invalid: unresolved EntityRef

def rejected(query: mv.Query, artifact: mv.Artifact, field: Deferred, metric: Deferred, relationship: Relationship) -> None:
    session.observe({'value': artifact})  # invalid: terminal metric input
    session.observe({'value': metric}, by=(artifact,))  # invalid: grouping stage
    session.observe({'value': metric}, via=(relationship,))  # invalid: observer route injection
    session.observe({'value': metric}, where=query['value'] > 0)  # invalid: result predicate at source
    session.query(artifact)  # invalid: terminal domain input
    session.read(artifact, {'category': field})  # invalid: terminal attribute input
    session.read(population, {'category': ms.ref.dimension('sales.customer.region')})  # invalid: bare field ref
    session.values(by=(), columns={'budget': {'unit': 'CNY'}}, rows=(((), {'budget': 1}),))  # invalid: nested column declaration
    query.where(True)  # invalid: ordinary boolean predicate
    query.select({'value': field})  # invalid: source field in result selection
    query.reduce({'value': query['value']})  # invalid: unfinished reduction shape
    query.top('1', by=query['value'])  # invalid: rank count type
    query.top(1, by=field)  # invalid: source score at result
    query.compare(artifact)  # invalid: terminal compare endpoint
    query.align(artifact)  # invalid: terminal alignment endpoint
    query['value'].divide(1, zero_denominator='zero')  # invalid: denominator policy
    mv.broadcast(artifact, to=query)  # invalid: reference stage
    mv.broadcast(query['value'], to=artifact)  # invalid: target stage
    mv.corresponding(via=relationship)  # invalid: non-tuple path
    artifact.execute()  # invalid: terminal execution
    artifact.contract()  # invalid: terminal continuation contract
    artifact.population()  # invalid: terminal domain continuation
    artifact.where(query['value'] > 0)  # invalid: terminal filtering
    population.read({'category': field})  # invalid: duplicate attribute entry
    population.observe({'value': metric})  # invalid: duplicate observation entry
"""
    source.write_text(program)
    root = PROJECT_ROOT
    completed = subprocess.run(
        [
            str(root / ".venv/bin/mypy"),
            "--no-pretty",
            "--no-color-output",
            "--cache-dir",
            str(tmp_path / "mypy-cache"),
            "--python-version",
            "3.10",
            str(source),
        ],
        cwd=root,
        env={**os.environ, "PYTHONPATH": str(root)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode != 0
    for line, text in enumerate(program.splitlines(), 1):
        if "# invalid:" in text:
            assert f"{source}:{line}: error:" in completed.stdout, (
                text + "\n" + completed.stdout + completed.stderr
            )


def test_public_observation_allows_omitted_window_and_precise_narrowing(tmp_path: Path) -> None:
    source = tmp_path / "valid_observation.py"
    source.write_text(
        """
import marivo.analysis as mv
import marivo.semantic as ms
from ibis.common.deferred import Deferred
from marivo.semantic.expressions import Entity
from typing_extensions import assert_type

session = mv.session.get_or_create('static', report_timezone='UTC')
population = session.members(ms.ref.entity('sales.customer'))
assert_type(population, mv.Population)
resolved_entity = session.catalog.entities.get(ms.ref.entity('sales.customer'))
assert_type(resolved_entity, Entity)
assert_type(mv.identities(resolved_entity, keys=(('C1',),)), mv.Population)
amount: Deferred
region: Deferred
query = session.observe({'revenue': amount.sum()}, by=(region,))
assert_type(query, mv.Query)
assert_type(query.population(), mv.Population)
assert_type(session.query(query.population()), mv.Query)
assert_type(query.execute(), mv.Artifact)
assert_type(population.project(by=()), mv.Population)
assert_type(population.where(mv.in_population(population)), mv.Population)
assert_type(session.observe({'revenue': amount.sum()}, during=None), mv.Query)

def current_receivers(query: mv.Query, other: mv.Query, field: Deferred) -> None:
    assert_type(query['revenue'], mv.Column)
    assert_type(query.select({'net': query['revenue'] - 1}), mv.Query)
    assert_type(query.where(query['revenue'] > 0), mv.Query)
    assert_type(query.top(3, by=query['revenue']), mv.Query)
    assert_type(query.reduce({'rows': mv.row_count(), 'mean': query['revenue'].mean()}), mv.Query)
    assert_type(query.rollup(), mv.Query)
    assert_type(query.drill(add=(field,)), mv.Query)
    assert_type(query.compare(other, match=mv.ordinal(), how='outer'), mv.Query)
    assert_type(mv.broadcast(other['revenue'], to=query), mv.Column)
    assert_type(session.read(population, {'category': field}), mv.Query)
"""
    )
    root = PROJECT_ROOT
    completed = subprocess.run(
        [
            str(root / ".venv/bin/mypy"),
            "--no-pretty",
            "--cache-dir",
            str(tmp_path / "mypy-cache"),
            "--python-version",
            "3.10",
            str(source),
        ],
        cwd=root,
        env={**os.environ, "PYTHONPATH": str(root)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
