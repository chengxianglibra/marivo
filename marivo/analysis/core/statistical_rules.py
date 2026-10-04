"""Closed association/forecast derivations and original scope declarations."""

from dataclasses import replace
from typing import Literal

from marivo.analysis.core.model import (
    AssociationStatePart,
    Coordinate,
    DerivedQuantity,
    DomainSignature,
    FindingPolicyPart,
    ForecastStatePart,
    FutureCellsPart,
    GridCellsPart,
    PairInputsPart,
    Part,
    Signature,
    TableFitColumn,
    TableFitsPart,
    TrainingInputsPart,
    require_part,
)
from marivo.analysis.core.rules import (
    AssociationFit,
    AssociationRead,
    ForecastFit,
    ForecastRead,
    RuleDerivation,
    _binding,
    _result,
)
from marivo.analysis.methods.errors import reject


def validate(part: Part) -> None:
    if (
        not isinstance(
            part,
            (
                PairInputsPart,
                AssociationStatePart,
                TrainingInputsPart,
                ForecastStatePart,
                FutureCellsPart,
            ),
        )
        or part.version != "v1"
    ):
        reject("closed statistical part at v1", repr(part), "Use the original statistical capture.")
    if isinstance(part, PairInputsPart) and (
        not 2 <= len(part.quantities) <= 16
        or len(part.quantities) != len(part.input_types)
        or len(part.quantities) != len(part.input_domains)
        or not part.lags
        or len(set(part.lags)) != len(part.lags)
        or any(type(k) is not int or not -(2**63) <= k < 2**63 for k in part.lags)
    ):
        reject(
            "ordered quantities and unique signed int64 lags",
            repr(part),
            "Reconstruct the original correlation.",
        )
    identity = (
        part.association_id
        if isinstance(part, (PairInputsPart, AssociationStatePart))
        else part.forecast_id
    )
    if not identity:
        reject(
            "exact statistical identity", repr(part), "Reconstruct the original statistical node."
        )


def corresponding_domains(left: DomainSignature, right: DomainSignature) -> bool:
    """Require the same coordinate and capture authority before realized key checks."""
    return left == right or (
        left.kind == right.kind
        and left.kind in ("entity", "group")
        and left == replace(right, definition_id=left.definition_id)
    )


def derive(
    inputs: tuple[Signature, ...],
    params: AssociationFit | AssociationRead | ForecastFit | ForecastRead,
) -> RuleDerivation:
    binding = _binding(inputs, "analysis.statistics")
    source = inputs[0]
    if isinstance(params, AssociationRead):
        retained = require_part(source, "pair_inputs")
        assert isinstance(retained, PairInputsPart)
        parts = tuple(
            replace(p, view=params.field) if isinstance(p, AssociationStatePart) else p
            for p in source.parts
        )
        quantity = DerivedQuantity(
            retained.association_id + ":" + params.field,
            "association.read@v1",
            tuple(q.definition_id for q in retained.quantities),
            "1" if params.field == "coefficient" else None,
            retained.quantities[0].time_scope,
            "descriptive_association",
        )
        return _result(
            "association_score@v1",
            inputs,
            source.domain,
            quantity,
            parts,
            pre=(),
            required=("pair_inputs", "association_state"),
            created=(),
            post=(),
            obligations=(),
            eval_id="association.read@v1",
        )
    if isinstance(params, ForecastRead):
        retained = require_part(source, "training_inputs")
        assert isinstance(retained, TrainingInputsPart)
        parts = tuple(
            replace(p, view=params.field) if isinstance(p, ForecastStatePart) else p
            for p in source.parts
        )
        quantity = DerivedQuantity(
            retained.forecast_id + ":" + params.field,
            "forecast.read@v1",
            (retained.quantity.definition_id,),
            retained.quantity.unit,
            source.quantity.time_scope if source.quantity else "future",
            "model_prediction" if params.field == "prediction" else "prediction_interval_bound",
        )
        return _result(
            "forecast@v1",
            inputs,
            source.domain,
            quantity,
            parts,
            pre=(),
            required=("training_inputs", "forecast_state", "future_cells"),
            created=(),
            post=(),
            obligations=(),
            eval_id="forecast.read@v1",
        )
    if source.quantity is None or any(s.quantity is None for s in inputs):
        reject("numeric quantities", "a domain", "Observe numeric quantities first.")
    if isinstance(params, AssociationFit):
        quantities = tuple(s.quantity for s in inputs if s.quantity is not None)
        if (
            not 2 <= len(inputs) <= 16
            or len({q.definition_id for q in quantities}) != len(inputs)
            or any(not corresponding_domains(s.domain, source.domain) for s in inputs)
        ):
            reject(
                "2..16 distinct quantities with exact common domain",
                repr(inputs),
                "Bind all quantities to the same complete observation domain.",
            )
        if not source.domain.instance_key or (
            params.explicit_lag and source.domain.time_grid is None
        ):
            reject(
                "non-scalar units; explicit lag requires original time grid",
                repr(source.domain),
                "Use complete time observations for lag.",
            )
        key = (
            Coordinate(
                source.domain.instance_key[0].entity_ref,
                "association:" + params.association_id,
                "instance",
            ),
        )
        domain = DomainSignature(binding, "group", key, key, params.association_id)
        declaration = PairInputsPart(
            binding,
            source.domain,
            quantities,
            params.input_types,
            params.association_id,
            params.method,
            params.lags,
            params.explicit_lag,
            tuple(s.domain for s in inputs),
        )
        producer: Literal["association.pearson", "association.spearman", "association.kendall"] = (
            "association.pearson"
            if params.method == "pearson"
            else "association.spearman"
            if params.method == "spearman"
            else "association.kendall"
        )
        parts = (
            declaration,
            AssociationStatePart(binding, params.association_id),
            FindingPolicyPart(
                binding,
                producer,
                "graph.association_findings@v1",
                "bounded_descriptive_findings@v1",
            ),
            *(
                (GridCellsPart(binding, source.domain, params.association_id),)
                if source.domain.time_grid is not None
                else ()
            ),
        )
        quantity = DerivedQuantity(
            params.association_id + ":coefficient",
            producer + "@v1",
            tuple(q.definition_id for q in quantities),
            "1",
            source.quantity.time_scope,
            "descriptive_association",
        )
        return _result(
            "association_score@v1",
            inputs,
            domain,
            quantity,
            parts,
            pre=(),
            required=(),
            created=(
                "pair_inputs",
                "association_state",
                "finding_policy",
                *(("grid_cells",) if source.domain.time_grid is not None else ()),
            ),
            post=(),
            obligations=(),
            eval_id=producer + "@v1",
        )
    assert isinstance(params, ForecastFit)
    grid = source.domain.time_grid
    if (
        grid is None
        or any(c.partial for c in grid.cells)
        or params.future.cells[0].start != grid.cells[-1].end
    ):
        reject(
            "complete history and approved adjacent future grid",
            repr(grid),
            "Use the original full time observation and certified future periods.",
        )
    domain = replace(
        source.domain,
        definition_id=params.forecast_id,
        time_grid=params.future,
        instance_key=tuple(
            replace(c, field="time:" + params.future.identity) if c.role == "anchor" else c
            for c in source.domain.instance_key
        ),
        target_key=tuple(
            replace(c, field="time:" + params.future.identity) if c.role == "anchor" else c
            for c in source.domain.target_key
        ),
    )
    forecast_producer: Literal["forecast.naive", "forecast.drift", "forecast.seasonal_naive"] = (
        "forecast.naive"
        if params.model == "naive"
        else "forecast.drift"
        if params.model == "drift"
        else "forecast.seasonal_naive"
    )
    parts = (
        TrainingInputsPart(
            binding,
            source.domain,
            source.quantity,
            params.input_type,
            params.forecast_id,
            params.model,
            params.season,
            params.level,
        ),
        ForecastStatePart(binding, params.forecast_id),
        FutureCellsPart(binding, params.forecast_id, params.future),
        FindingPolicyPart(
            binding,
            forecast_producer,
            "graph.forecast_findings@v1",
            "bounded_prediction_findings@v1",
        ),
        GridCellsPart(binding, source.domain, params.forecast_id),
    )
    quantity = DerivedQuantity(
        params.forecast_id + ":prediction",
        forecast_producer + "@v1",
        (source.quantity.definition_id,),
        source.quantity.unit,
        "future:" + params.future.identity,
        "model_prediction",
    )
    return _result(
        "forecast@v1",
        inputs,
        domain,
        quantity,
        parts,
        pre=(),
        required=(),
        created=(
            "training_inputs",
            "forecast_state",
            "future_cells",
            "finding_policy",
            "grid_cells",
        ),
        post=(),
        obligations=(),
        eval_id=forecast_producer + "@v1",
    )


def table_parts(inputs: tuple[Signature, ...]) -> tuple[Part, ...]:
    """Retain one exact statistical scope for a table of its owned views."""
    ranked = any(
        item.quantity is not None and item.quantity.method_version == "display.ranks@v1"
        for item in inputs
    )
    captured = (
        (
            TableFitsPart(
                inputs[0].domain.binding,
                tuple(TableFitColumn(i, item) for i, item in enumerate(inputs)),
            ),
        )
        if ranked
        else ()
    )
    association = tuple(
        next((p for p in item.parts if isinstance(p, AssociationStatePart)), None)
        for item in inputs
    )
    first_a = association[0]
    if first_a is not None and all(
        p is not None and p.association_id == first_a.association_id for p in association
    ):
        a_views: tuple[Literal["coefficient", "selected"], ...] = tuple(
            "selected" if p.view == "selected" else "coefficient"
            for p in association
            if p is not None
        )
        return (
            *captured,
            *tuple(
                replace(p, table_views=() if ranked else a_views)
                if isinstance(p, AssociationStatePart)
                else p
                for p in inputs[0].parts
                if isinstance(
                    p, (PairInputsPart, AssociationStatePart, FindingPolicyPart, GridCellsPart)
                )
            ),
        )
    forecasts = tuple(
        next((p for p in item.parts if isinstance(p, ForecastStatePart)), None) for item in inputs
    )
    first_f = forecasts[0]
    if first_f is not None and all(
        p is not None and p.forecast_id == first_f.forecast_id for p in forecasts
    ):
        f_views: tuple[Literal["prediction", "lower", "upper"], ...] = tuple(
            "prediction" if p.view == "result" else p.view for p in forecasts if p is not None
        )
        return (
            *captured,
            *tuple(
                replace(p, table_views=() if ranked else f_views)
                if isinstance(p, ForecastStatePart)
                else p
                for p in inputs[0].parts
                if isinstance(
                    p,
                    (
                        TrainingInputsPart,
                        ForecastStatePart,
                        FutureCellsPart,
                        FindingPolicyPart,
                        GridCellsPart,
                    ),
                )
            ),
        )
    return ()
