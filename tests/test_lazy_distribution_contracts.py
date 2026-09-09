"""No-I/O admission, closed authority and semantic approximation boundaries."""

import pytest

from marivo.analysis.compiler.placement import place
from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.analysis.materialization.publication import materialization_contract
from marivo.analysis.observation.distribution_contracts import distribution_part_authorities
from marivo.analysis.observation.fold_contracts import FoldAuthorityV1, decode_fold_authority
from marivo.analysis.operators.attribute import attribute_method
from marivo.analysis.operators.attribution_contracts import AttributionSemantics
from marivo.analysis.session._lazy_sources import make_lazy_sources
from marivo.semantic._quantile import quantile_metric
from tests.lazy_distribution_fixtures import CHANNEL, METRIC, make_distribution_registry
from tests.lazy_observation_fixtures import NoIoActionPort


def test_exact_and_approximate_definitions_are_distinct_and_do_not_mix() -> None:
    registry, sidecar = make_distribution_registry()
    source = make_lazy_sources(
        semantic_registry=registry,
        sidecar=sidecar,
        action_port=NoIoActionPort(),
        session_id="contracts",
        store_id="contracts",
    )
    exact = source.observe(METRIC).with_dimensions(CHANNEL).aggregate()
    approximate = (
        source.observe(quantile_metric(METRIC, method="duckdb_tdigest@v1"))
        .with_dimensions(CHANNEL)
        .aggregate()
    )
    assert exact.definition_fingerprint != approximate.definition_fingerprint
    with pytest.raises(DatasetConstructionError, match="quantile method input at projection"):
        exact.metric(quantile_metric(METRIC, method="duckdb_tdigest@v1"))
    with pytest.raises(DatasetConstructionError, match="incompatible Metric contracts"):
        exact.compare(approximate)
    for metric, approximation in ((exact, "exact"), (approximate, "semantic_percentile")):
        projected = metric.metric(METRIC)
        assert distribution_part_authorities(
            projected.row_contract
        ) == distribution_part_authorities(metric.row_contract)
        projected_semantics = (
            projected.compare(projected).attribute(axes=(CHANNEL,)).row_contract.family_semantics
        )
        assert isinstance(projected_semantics, AttributionSemantics)
        assert projected_semantics.approximation_class == approximation
        roles = distribution_part_authorities(metric.row_contract)
        assert len(roles) == 1 and roles[0][1].distribution is not None
        assert (
            "metric.distribution"
            in materialization_contract(metric).retained_private_state_contract_ids
        )
        output = metric.compare(metric).attribute(axes=(CHANNEL,))
        assert isinstance(output.row_contract.family_semantics, AttributionSemantics)
        assert output.row_contract.family_semantics.approximation_class == approximation
        assert output.row_contract.family_semantics.rollup_safe is False
        assert len(place(output).local_steps) == 1
        with pytest.raises(DatasetConstructionError):
            attribute_method(
                roles[0][1].model_copy(
                    update={"axis_partitions": ((CHANNEL.path, "overlapping"),)}
                ),
                (CHANNEL.path,),
            )
        authority = roles[0][1]
        for broken in (
            authority.model_copy(update={"root_id": "missing"}),
            authority.model_copy(
                update={
                    "components": (
                        authority.components[0].model_copy(update={"spatial_merge": "sum"}),
                    )
                }
            ),
        ):
            with pytest.raises(ValueError):
                decode_fold_authority(
                    FoldAuthorityV1(schema_version=1, metrics=(broken,)).to_json()
                )


def test_nonpercentile_explicit_method_rejected_without_execution() -> None:
    registry, sidecar = make_distribution_registry()
    source = make_lazy_sources(
        semantic_registry=registry,
        sidecar=sidecar,
        action_port=NoIoActionPort(),
        session_id="contracts",
        store_id="contracts",
    )
    from marivo.refs import ref

    with pytest.raises(DatasetConstructionError, match="governed root median or percentile"):
        source.observe(quantile_metric(ref.metric("sales.order_count"), method="duckdb_tdigest@v1"))


def test_entity_scope_has_no_distribution_local_preparation() -> None:
    registry, sidecar = make_distribution_registry()
    source = make_lazy_sources(
        semantic_registry=registry,
        sidecar=sidecar,
        action_port=NoIoActionPort(),
        session_id="entity",
        store_id="entity",
    )
    metric = source.observe(METRIC)
    with pytest.raises(DatasetConstructionError, match="Entity"):
        metric.compare(metric).attribute(axes=(CHANNEL,))
