"""Tests for metric v2 semantic object models."""

import pytest
from pydantic import ValidationError

from app.api.models.base import AdditivityConstraints
from app.api.models.metric import (
    AverageMetricPayload,
    CountMetricPayload,
    DistributionMetricPayload,
    DistributionSpec,
    MeasurementComponent,
    MetricHeader,
    MetricPayload,
    RateMetricPayload,
    ScoreMetricPayload,
    SumMetricPayload,
    SurvivalMetricPayload,
    SurvivalSpec,
    TypedMetricCreateRequest,
    TypedMetricResponse,
)


class TestMetricHeader:
    """Tests for MetricHeader model."""

    def test_valid_header(self):
        header = MetricHeader(
            metric_ref="metric.dau",
            display_name="Daily Active Users",
            metric_family="count_metric",
            observed_entity_ref="entity.user",
            observation_grain_ref="grain.user",
            sample_kind="numeric",
            value_semantics="count",
            additivity_constraints=AdditivityConstraints(
                dimension_policy="none", time_axis_policy="non_additive"
            ),
            metric_contract_version="metric.v1",
        )
        assert header.metric_ref == "metric.dau"
        assert header.metric_family == "count_metric"
        assert header.observed_entity_ref == "entity.user"

    def test_header_with_population_subject(self):
        header = MetricHeader(
            metric_ref="metric.conversion_rate",
            metric_family="rate_metric",
            population_subject_ref="subject.user",
            observed_entity_ref="entity.user",
            observation_grain_ref="grain.user",
            sample_kind="rate",
            value_semantics="ratio",
            additivity_constraints=AdditivityConstraints(
                dimension_policy="none", time_axis_policy="non_additive"
            ),
            metric_contract_version="metric.v1",
        )
        assert header.population_subject_ref == "subject.user"

    def test_invalid_metric_ref_prefix(self):
        with pytest.raises(ValidationError, match=r"'metric_ref' must start with 'metric\.'"):
            MetricHeader(
                metric_ref="wrong.dau",
                metric_family="count_metric",
                observed_entity_ref="entity.user",
                observation_grain_ref="grain.user",
                sample_kind="numeric",
                value_semantics="count",
                additivity_constraints=AdditivityConstraints(
                    dimension_policy="none", time_axis_policy="non_additive"
                ),
                metric_contract_version="metric.v1",
            )

    def test_invalid_observed_entity_ref_prefix(self):
        with pytest.raises(
            ValidationError,
            match=r"'observed_entity_ref' must start with 'entity\.'",
        ):
            MetricHeader(
                metric_ref="metric.dau",
                metric_family="count_metric",
                observed_entity_ref="wrong.user",
                observation_grain_ref="grain.user",
                sample_kind="numeric",
                value_semantics="count",
                additivity_constraints=AdditivityConstraints(
                    dimension_policy="none", time_axis_policy="non_additive"
                ),
                metric_contract_version="metric.v1",
            )

    def test_invalid_observation_grain_ref_prefix(self):
        with pytest.raises(
            ValidationError,
            match=r"'observation_grain_ref' must start with 'grain\.'",
        ):
            MetricHeader(
                metric_ref="metric.dau",
                metric_family="count_metric",
                observed_entity_ref="entity.user",
                observation_grain_ref="wrong.user",
                sample_kind="numeric",
                value_semantics="count",
                additivity_constraints=AdditivityConstraints(
                    dimension_policy="none", time_axis_policy="non_additive"
                ),
                metric_contract_version="metric.v1",
            )


class TestMeasurementComponent:
    """Tests for MeasurementComponent model."""

    def test_valid_component(self):
        comp = MeasurementComponent(
            name="active_users",
            semantics="distinct active users",
            aggregation="count_distinct",
            measure_ref="measure.active_user",
            input_field_ref="entity.user.field.user_id",
        )
        assert comp.name == "active_users"
        assert comp.aggregation == "count_distinct"
        assert comp.input_field_ref == "entity.user.field.user_id"

    def test_with_qualifier_refs(self):
        comp = MeasurementComponent(
            name="converted_users",
            semantics="users who converted",
            aggregation="count_distinct",
            qualifier_refs=["predicate.converted"],
        )
        assert comp.qualifier_refs == ["predicate.converted"]

    def test_rejects_unqualified_input_field_ref(self):
        with pytest.raises(ValidationError, match="fully qualified entity field"):
            MeasurementComponent(
                name="converted_users",
                semantics="users who converted",
                aggregation="count_distinct",
                input_field_ref="field.user_id",
            )

    def test_rejects_physical_component_binding_fields(self):
        with pytest.raises(ValidationError):
            MeasurementComponent(
                name="converted_users",
                semantics="users who converted",
                aggregation="count_distinct",
                physical_column="converted_users",
            )


class TestDistributionSpec:
    """Tests for DistributionSpec model."""

    def test_valid_percentile(self):
        spec = DistributionSpec(kind="percentile", percentile=0.95)
        assert spec.kind == "percentile"
        assert spec.percentile == 0.95

    @pytest.mark.parametrize("percentile", [-1, 0, 1, 95])
    def test_reject_invalid_percentile_range(self, percentile):
        with pytest.raises(ValidationError, match="percentile must satisfy 0 < p < 1"):
            DistributionSpec(kind="percentile", percentile=percentile)

    def test_reject_missing_percentile_for_percentile_kind(self):
        with pytest.raises(
            ValidationError,
            match="percentile must be provided when kind is 'percentile'",
        ):
            DistributionSpec(kind="percentile")


class TestMetricPayloads:
    """Tests for family-specific metric payloads."""

    def test_count_metric_payload(self):
        payload = CountMetricPayload(
            count_target=MeasurementComponent(
                name="users",
                semantics="distinct users",
                aggregation="count_distinct",
            )
        )
        assert payload.metric_family == "count_metric"

    def test_sum_metric_payload(self):
        payload = SumMetricPayload(
            measure=MeasurementComponent(
                name="revenue",
                semantics="total revenue",
                aggregation="sum",
            )
        )
        assert payload.metric_family == "sum_metric"

    def test_rate_metric_payload(self):
        payload = RateMetricPayload(
            numerator=MeasurementComponent(
                name="conversions",
                semantics="conversion events",
                aggregation="count_distinct",
            ),
            denominator=MeasurementComponent(
                name="users",
                semantics="total users",
                aggregation="count_distinct",
            ),
        )
        assert payload.metric_family == "rate_metric"
        assert payload.numerator.name == "conversions"

    def test_average_metric_payload(self):
        payload = AverageMetricPayload(
            numerator=MeasurementComponent(
                name="total_time",
                semantics="total watch time",
                aggregation="sum",
            ),
            denominator=MeasurementComponent(
                name="session_count",
                semantics="number of sessions",
                aggregation="count_distinct",
            ),
        )
        assert payload.metric_family == "average_metric"

    def test_distribution_metric_payload(self):
        payload = DistributionMetricPayload(
            value_component=MeasurementComponent(
                name="latency",
                semantics="request latency",
                aggregation="mean",
            ),
            distribution_spec=DistributionSpec(kind="percentile", percentile=0.95),
        )
        assert payload.metric_family == "distribution_metric"

    def test_score_metric_payload(self):
        payload = ScoreMetricPayload(
            score_source=MeasurementComponent(
                name="fraud_score",
                semantics="fraud risk score",
                aggregation="mean",
            ),
            score_kind="model_output",
        )
        assert payload.metric_family == "score_metric"
        assert payload.score_kind == "model_output"

    def test_survival_metric_payload(self):
        payload = SurvivalMetricPayload(
            survival_spec=SurvivalSpec(
                origin_time_ref="time.signup_time",
                event_time_ref="time.churn_time",
                event_definition_ref="event.churn",
            )
        )
        assert payload.metric_family == "survival_metric"


class TestTypedMetricCreateRequest:
    """Tests for TypedMetricCreateRequest with discriminated union."""

    def test_valid_count_metric_request(self):
        request = TypedMetricCreateRequest(
            header=MetricHeader(
                metric_ref="metric.dau",
                metric_family="count_metric",
                observed_entity_ref="entity.user",
                observation_grain_ref="grain.user",
                sample_kind="numeric",
                value_semantics="count",
                additivity_constraints=AdditivityConstraints(
                    dimension_policy="none", time_axis_policy="non_additive"
                ),
                metric_contract_version="metric.v1",
            ),
            payload=CountMetricPayload(
                count_target=MeasurementComponent(
                    name="users",
                    semantics="distinct users",
                    aggregation="count_distinct",
                )
            ),
            catalog_metadata={"domain_ref": "domain.growth", "aliases": ["DAU"]},
        )
        assert request.header.metric_family == "count_metric"
        assert request.catalog_metadata.domain_ref == "domain.growth"
        assert request.catalog_metadata.aliases == ["DAU"]

    def test_create_request_rejects_invalid_catalog_domain_ref(self):
        with pytest.raises(ValidationError, match=r"'domain_ref' must start with 'domain\.'"):
            TypedMetricCreateRequest(
                header=MetricHeader(
                    metric_ref="metric.dau",
                    metric_family="count_metric",
                    observed_entity_ref="entity.user",
                    observation_grain_ref="grain.user",
                    sample_kind="numeric",
                    value_semantics="count",
                    additivity_constraints=AdditivityConstraints(
                        dimension_policy="none", time_axis_policy="non_additive"
                    ),
                    metric_contract_version="metric.v1",
                ),
                payload=CountMetricPayload(
                    count_target=MeasurementComponent(
                        name="users",
                        semantics="distinct users",
                        aggregation="count_distinct",
                    )
                ),
                catalog_metadata={"domain_ref": "metric.dau"},
            )

    def test_valid_rate_metric_request(self):
        request = TypedMetricCreateRequest(
            header=MetricHeader(
                metric_ref="metric.conversion_rate",
                metric_family="rate_metric",
                observed_entity_ref="entity.user",
                observation_grain_ref="grain.user",
                sample_kind="rate",
                value_semantics="ratio",
                additivity_constraints=AdditivityConstraints(
                    dimension_policy="none", time_axis_policy="non_additive"
                ),
                metric_contract_version="metric.v1",
            ),
            payload=RateMetricPayload(
                numerator=MeasurementComponent(
                    name="conversions",
                    semantics="converted users",
                    aggregation="count_distinct",
                    input_field_ref="entity.conversion_event.field.converted_users",
                ),
                denominator=MeasurementComponent(
                    name="users",
                    semantics="total users",
                    aggregation="count_distinct",
                    input_field_ref="entity.exposure_event.field.exposed_users",
                ),
            ),
        )
        assert request.header.metric_family == "rate_metric"
        assert (
            request.payload.numerator.input_field_ref
            == "entity.conversion_event.field.converted_users"
        )

    def test_family_semantics_mismatch_rejected(self):
        """value_semantics must match metric_family."""
        with pytest.raises(ValidationError, match="value_semantics must be 'count'"):
            TypedMetricCreateRequest(
                header=MetricHeader(
                    metric_ref="metric.dau",
                    metric_family="count_metric",
                    observed_entity_ref="entity.user",
                    observation_grain_ref="grain.user",
                    sample_kind="numeric",
                    value_semantics="sum",  # Wrong!
                    additivity_constraints=AdditivityConstraints(
                        dimension_policy="none", time_axis_policy="non_additive"
                    ),
                    metric_contract_version="metric.v1",
                ),
                payload=CountMetricPayload(
                    count_target=MeasurementComponent(
                        name="users",
                        semantics="distinct users",
                        aggregation="count_distinct",
                    )
                ),
            )

    def test_family_payload_mismatch_rejected(self):
        """header.metric_family must match payload.metric_family."""
        with pytest.raises(ValidationError, match=r"header\.metric_family"):
            TypedMetricCreateRequest(
                header=MetricHeader(
                    metric_ref="metric.dau",
                    metric_family="count_metric",
                    observed_entity_ref="entity.user",
                    observation_grain_ref="grain.user",
                    sample_kind="numeric",
                    value_semantics="count",
                    additivity_constraints=AdditivityConstraints(
                        dimension_policy="none", time_axis_policy="non_additive"
                    ),
                    metric_contract_version="metric.v1",
                ),
                payload=SumMetricPayload(  # Wrong family!
                    measure=MeasurementComponent(
                        name="revenue",
                        semantics="total revenue",
                        aggregation="sum",
                    )
                ),
            )


class TestMetricPayloadUnion:
    """Tests for MetricPayload discriminated union."""

    def test_union_selects_count(self):
        payload: MetricPayload = CountMetricPayload(
            count_target=MeasurementComponent(
                name="users",
                semantics="users",
                aggregation="count_distinct",
            )
        )
        assert payload.metric_family == "count_metric"

    def test_union_selects_rate(self):
        payload: MetricPayload = RateMetricPayload(
            numerator=MeasurementComponent(
                name="conversions",
                semantics="conversions",
                aggregation="count_distinct",
            ),
            denominator=MeasurementComponent(
                name="users",
                semantics="users",
                aggregation="count_distinct",
            ),
        )
        assert payload.metric_family == "rate_metric"


class TestTypedMetricResponse:
    """Tests for TypedMetricResponse model."""

    def test_valid_response(self):
        response = TypedMetricResponse(
            metric_contract_id="mc_123",
            header=MetricHeader(
                metric_ref="metric.dau",
                metric_family="count_metric",
                observed_entity_ref="entity.user",
                observation_grain_ref="grain.user",
                sample_kind="numeric",
                value_semantics="count",
                additivity_constraints=AdditivityConstraints(
                    dimension_policy="none", time_axis_policy="non_additive"
                ),
                metric_contract_version="metric.v1",
            ),
            payload=CountMetricPayload(
                count_target=MeasurementComponent(
                    name="users",
                    semantics="users",
                    aggregation="count_distinct",
                )
            ),
            status="draft",
            lifecycle_status="draft",
            readiness_status="not_ready",
            blocking_requirements=[],
            capabilities={},
            revision=1,
            created_at="2024-01-01T00:00:00Z",
            updated_at="2024-01-01T00:00:00Z",
        )
        assert response.metric_contract_id == "mc_123"
        assert response.status == "draft"
        assert response.lifecycle_status == "draft"
