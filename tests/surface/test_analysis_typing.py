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
session = mv.session.create('static', timezone='UTC')
population = session.members(session.catalog.entities.get('sales.customer'))
session.members(ms.ref.metric('sales.revenue'))  # invalid: entity kind
mv.coordinates(by=(mv.identity(ms.ref.entity('sales.customer')),), keys=((('C1',),),))  # invalid: unresolved EntityRef

def rejected(query: mv.AnalysisFrame, artifact: mv.Artifact, field: Deferred, metric: Deferred, relationship: Relationship) -> None:
    session.observe({'value': artifact})  # invalid: terminal metric input
    session.observe({'value': metric}, by=(artifact,))  # invalid: grouping stage
    session.observe({'value': metric}, via=(relationship,))  # invalid: observer route injection
    session.observe({'value': metric}, where=query['value'] > 0)  # invalid: result predicate at source
    session.frame(artifact)  # invalid: terminal domain input
    session.read(artifact, {'category': field})  # invalid: terminal attribute input
    session.read(population, {'category': ms.ref.dimension('sales.customer.region')})  # invalid: bare field ref
    session.values(by=(), columns={'budget': {'unit': 'CNY'}}, rows=(((), {'budget': 1}),))  # invalid: nested column declaration
    query.where(True)  # invalid: ordinary boolean predicate
    query.select({'value': field})  # invalid: source field in result selection
    query.reduce({'value': query['value']})  # invalid: unfinished reduction shape
    query.top('1', by=query['value'])  # invalid: rank count type
    query.top(1, by=field)  # invalid: source score at result
    query.compare({'reference': artifact})  # invalid: terminal compare endpoint
    query.compare(query)  # invalid: retired bare baseline
    mv.previous_year()  # invalid: retired rule constructor
    mv.period_offset(-1)  # invalid: retired rule constructor
    mv.offset()  # invalid: missing offset
    mv.offset(years=-1, periods=-1)  # invalid: conflicting offset modes
    mv.offset(-1)  # invalid: positional offset
    mv.offset(years=None)  # invalid: missing integer
    query.align(artifact)  # invalid: terminal alignment endpoint
    query['value'].divide(1, zero_denominator='zero')  # invalid: denominator policy
    mv.broadcast(artifact, to=query)  # invalid: reference stage
    mv.broadcast(query['value'], to=artifact)  # invalid: target stage
    mv.corresponding  # invalid: retired identity correspondence
    mv.Query  # invalid: retired result type
    mv.broadcast  # invalid: retired broadcast
    mv.CorrespondenceAxis  # invalid: retired axis
    session.population_from(query)  # invalid: required source map
    session.population_from(query, on=((query.axes[0], 'field'),))  # invalid: string semantic target
    session.population_from(query, on=(('axis', field),))  # invalid: string axis
    session.population_from(artifact, on=())  # invalid: audit is not data
    session.query(population)  # invalid: retired constructor
    session.artifact('id')  # invalid: retired data reopening
    session.persisted_frame(artifact)  # invalid: audit object is not ID
    session.persisted_frame(mv.ArtifactRef('id'))  # invalid: ref object is not ID
    query.execute()  # invalid: retired execution
    query.population()  # invalid: retired implicit source bridge
    query.rollup()  # invalid: retired original Metric receiver
    query.drill(add=(field,))  # invalid: retired new-attribute receiver
    query.share('value')  # invalid: retired contribution receiver
    query.coord(field)  # invalid: source field is not FrameAxis
    query.reduce({'rows': mv.row_count()}, by=(field,))  # invalid: source grouping axis
    query.align(query, on=(('left', 'right'),))  # invalid: string axes
    query.top(1, by=query['value'], partition_by=())  # invalid: retired partition policy
    query.compare({'reference': query}, time_axis='current')  # invalid: retired paired projection
    artifact.to_pandas()  # invalid: data belongs to Frame
    artifact.show(n=0)  # invalid: audit has no preview count
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

session = mv.session.create('static', timezone='UTC')
population = session.members(session.catalog.entities.get('sales.customer'))
assert_type(population, mv.Population)
resolved_entity = session.catalog.entities.get('sales.customer')
assert_type(resolved_entity, Entity)
assert_type(mv.coordinates(by=(mv.identity(resolved_entity),), keys=((('C1',),),)), mv.Population)
amount: Deferred
region: Deferred
query = session.observe({'revenue': amount.sum()}, by=(region,))
assert_type(query, mv.AnalysisFrame)
assert_type(session.frame(population), mv.AnalysisFrame)
assert_type(query.compute(), mv.AnalysisFrame)
assert_type(query.persist(), mv.AnalysisFrame)
assert_type(population.project(by=()), mv.Population)
assert_type(population.where(mv.in_population(population)), mv.Population)
assert_type(session.observe({'revenue': amount.sum()}, by=()), mv.AnalysisFrame)

def current_receivers(query: mv.AnalysisFrame, other: mv.AnalysisFrame, field: Deferred) -> None:
    assert_type(session.population_from(query, on=((query.axes[0], field),)), mv.Population)
    assert_type(query['revenue'], mv.Column)
    assert_type(query.select({'net': query['revenue'] - 1}), mv.AnalysisFrame)
    assert_type(query.where(query['revenue'] > 0), mv.AnalysisFrame)
    assert_type(query.top(3, by=query['revenue']), mv.AnalysisFrame)
    assert_type(query.reduce({'rows': mv.row_count(), 'mean': query['revenue'].mean()}), mv.AnalysisFrame)
    assert_type(query.compare({'reference': other}, match={'reference': mv.ordinal()}, how='outer'), mv.AnalysisFrame)
    assert_type(session.read(population, {'category': field}), mv.AnalysisFrame)
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
