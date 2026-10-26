"""Independent rejected call shapes at the public typing boundary."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from tests.support.typing import check


@pytest.mark.slow
@pytest.mark.release
def test_public_analysis_and_deviation_typing_contracts(tmp_path: Path) -> None:
    probe_id = hashlib.sha256(str(tmp_path).encode()).hexdigest()[:12]
    source = tmp_path / f"bad_public_analysis_{probe_id}.py"
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
session = mv.session.get_or_create('static', timezone='UTC')
population = session.members(session.catalog.entities.get('sales.customer'))
session.members(ms.ref.metric('sales.revenue'))  # invalid: entity kind
mv.identity(ms.ref.datasource('warehouse'))  # invalid: Entity identity role

def rejected(query: mv.AnalysisFrame, artifact: mv.Artifact, field: Deferred, metric: Deferred, relationship: Relationship) -> None:
    session.observe({'value': artifact})  # invalid: terminal metric input
    session.observe({'value': metric}, by=(artifact,))  # invalid: grouping stage
    session.observe({'value': metric}, via=(relationship,))  # invalid: observer route injection
    session.observe({'value': metric}, where=query['value'] > 0)  # invalid: result predicate at source
    session.frame(artifact)  # invalid: terminal domain input
    session.read(artifact, {'category': field})  # invalid: terminal attribute input
    session.read(population, {'category': relationship})  # invalid: relationship is not a field
    session.values(by=(), columns={'budget': {'unit': 'CNY'}}, rows=(((), {'budget': 1}),))  # invalid: nested column declaration
    query.where(True)  # invalid: ordinary boolean predicate
    query.select({'value': field})  # invalid: source field in result selection
    query.reduce({'value': query['value']})  # invalid: unfinished reduction shape
    query.attribute()  # invalid: missing fine domain
    query.attribute(by=query.axes)  # invalid: Frame axes are not source classifications
    query.attribute(by=(field,), baseline=query)  # invalid: receiver owns its baseline
    query.over(query['value'])  # invalid: unfinished partition statistic
    query.over(mv.row_count(), by=(field,))  # invalid: source partition axis
    query.over(mv.row_count(), by=[query.axes[0]])  # invalid: partition axes are a tuple
    query['value'].share()  # invalid: missing normalization axes
    query['value'].share(along=(field,))  # invalid: source normalization axis
    query['value'].share(along=query.axes, zero_denominator='zero')  # invalid: share policy
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

    valid_source = tmp_path / f"valid_observation_{probe_id}.py"
    valid_source.write_text(
        """
import marivo.analysis as mv
import marivo.semantic as ms
from ibis.common.deferred import Deferred
from marivo.semantic.expressions import Entity
from typing_extensions import assert_type

session = mv.session.get_or_create('static', timezone='UTC')
population = session.members(session.catalog.entities.get('sales.customer'))
assert_type(population, mv.Population)
resolved_entity = session.catalog.entities.get('sales.customer')
assert_type(resolved_entity, Entity)
assert_type(mv.coordinates(by=(mv.identity(resolved_entity),), keys=((('C1',),),)), mv.Population | Deferred)
assert_type(mv.identity(ms.ref.entity('sales.customer')), mv.Grouping | Deferred)
assert_type(mv.coordinates(by=(ms.ref.dimension('sales.customer.region'),), keys=(('east',),)), mv.Population | Deferred)
amount: Deferred
region: Deferred
query = session.observe({'revenue': amount.sum()}, by=(region,))
assert_type(query, mv.AnalysisFrame)
assert_type(session.frame(population), mv.AnalysisFrame)
assert_type(query.compute(), mv.AnalysisFrame)
assert_type(query.persist(), mv.AnalysisFrame)
assert_type(population.project(by=()), mv.Population | Deferred)
assert_type(population.where(mv.in_population(population)), mv.Population | Deferred)
assert_type(session.observe({'revenue': amount.sum()}, by=()), mv.AnalysisFrame)

def current_receivers(query: mv.AnalysisFrame, other: mv.AnalysisFrame, field: Deferred) -> None:
    assert_type(session.population_from(query, on=((query.axes[0], field),)), mv.Population)
    assert_type(query['revenue'], mv.Column)
    assert_type(query.select({'net': query['revenue'] - 1}), mv.AnalysisFrame)
    assert_type(query.where(query['revenue'] > 0), mv.AnalysisFrame)
    assert_type(query.top(3, by=query['revenue']), mv.AnalysisFrame)
    assert_type(query.reduce({'rows': mv.row_count(), 'mean': query['revenue'].mean()}), mv.AnalysisFrame)
    assert_type(query.over(query['revenue'].sum(), by=query.axes), mv.Column)
    assert_type(query.over(mv.row_count()), mv.Column)
    assert_type(query['revenue'].share(along=query.axes), mv.Column)
    assert_type(query.compare({'reference': other}, match={'reference': mv.ordinal()}), mv.AnalysisFrame)
    assert_type(session.read(population, {'category': field}), mv.AnalysisFrame)
"""
    )

    statistics_source = tmp_path / f"statistics_contracts_{probe_id}.py"
    statistics_body = """import marivo.analysis as mv
import pandas as pd
from ibis.common.deferred import Deferred
from typing_extensions import assert_type

def positive(session: mv.Session, members: mv.Population, field: Deferred, other: mv.AnalysisFrame, artifact: mv.Artifact) -> None:
    query = session.observe({"value": field.mean()}, by=members)
    assert_type(query, mv.AnalysisFrame)
    assert_type(query["value"], mv.Column)
    assert_type(query.where(query["value"] > 0), mv.AnalysisFrame)
    assert_type(query.select({"centered": query["value"] - 10}), mv.AnalysisFrame)
    assert_type(query.top(3, by=query["value"]), mv.AnalysisFrame)
    assert_type(query.reduce({"mean": query["value"].mean(), "sum": query["value"].sum(), "rows": mv.row_count()}), mv.AnalysisFrame)
    assert_type(query.compare({'reference': other}), mv.AnalysisFrame)
    assert_type(query.align(other), mv.AnalysisFrame)
    assert_type(query.compute(), mv.AnalysisFrame)
    assert_type(query.persist().artifact, mv.Artifact)
    frame = session.persisted_frame(artifact.id).to_pandas()
    assert_type(frame, pd.DataFrame)
    assert_type(frame.corr(method="pearson", numeric_only=True), pd.DataFrame)
    assert_type(frame.corr(method="spearman", numeric_only=True), pd.DataFrame)
    assert_type(frame.corr(method="kendall", numeric_only=True), pd.DataFrame)
    assert_type(frame.describe(), pd.DataFrame)

def rejected(session: mv.Session, members: mv.Population, query: mv.AnalysisFrame, other: mv.AnalysisFrame, artifact: mv.Artifact, field: Deferred) -> None:
"""
    calls = (
        "query.deviation(method='zscore')",
        "query.deviation(method='mad', partition_by=(field,))",
        "query.runs(where=query['value'] > 0)",
        "query.correlate(query['value'], other, method='pearson')",
        "query.forecast(horizon=2)",
        "artifact.execute()",
        "artifact.where(query['value'] > 0)",
        "artifact.reduce({'value': query['value'].mean()})",
        "artifact.forecast(horizon=2)",
        "query.reduce({'value': query['value']})",
        "query.reduce({'value': query['value'].mean()}, by=[field])",
        "query.reduce({'value': query['value'].mean()}, by=(query['value'],))",
        "query.where(pd.Series([True]))",
        "query.select({'value': artifact})",
        "query.top(1, by='value')",
        "session.observe({'value': query.compute().to_pandas()}, by=members)",
        "mv.broadcast(query['value'], to=query)",
        "artifact.to_pandas()",
    )
    statistics_body += "".join(f"    {call}\n" for call in calls)
    statistics_source.write_text(statistics_body)

    # Run all public typing probes in one mypy process so xdist workers do not
    # repeat the same dependency analysis with separate cold caches.
    completed = check(source, valid_source, statistics_source)
    assert completed.returncode != 0
    output = completed.stdout + completed.stderr
    for line, text in enumerate(program.splitlines(), 1):
        if "# invalid:" in text:
            assert f"{source}:{line}: error:" in output, text + "\n" + output
    assert not any(
        f"{valid_source}:" in line and ": error:" in line for line in output.splitlines()
    )
    statistics_errors = [
        line
        for line in output.splitlines()
        if f"{statistics_source}:" in line and ": error:" in line
    ]
    error_locations = {line.split(": error:", maxsplit=1)[0] for line in statistics_errors}
    expected = {
        f"{statistics_source}:{statistics_body.splitlines().index('    ' + call) + 1}"
        for call in calls
    }
    assert error_locations == expected, output
    assert len(statistics_errors) == len(calls), output
