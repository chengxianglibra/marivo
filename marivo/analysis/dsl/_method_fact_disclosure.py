"""Declare the typed Frame method facts and their reading surface."""

from marivo.analysis.core.frame_level_facts import (
    FrameLevelAdditiveMethod,
    FrameLevelDivisionMethod,
    FrameLevelLinearMethod,
    FrameLevelLinearTerm,
    FrameLevelRatioMethod,
    FrameLevelSupport,
)
from marivo.analysis.core.frame_method_facts import (
    FrameAdditiveMethod,
    FrameAttributeMetric,
    FrameDivisionMethod,
    FrameLinearMethod,
    FrameLinearTerm,
    FrameMethodCoordinate,
    FrameMethodFacts,
    FrameMethodParent,
    FrameMethodSupport,
    FrameRatioMethod,
    FrameShapleyMethod,
)
from marivo.analysis.core.frame_shapley_facts import (
    FrameShapleyCapture,
    FrameShapleyGame,
    FrameShapleyLeaf,
    FrameShapleyNativeQuantile,
    FrameShapleyQuantile,
    FrameShapleyQuery,
    FrameShapleyQueryGame,
    FrameShapleyReduction,
)
from marivo.analysis.model.disclosure import (
    Descriptor,
    ExampleInput,
    bind,
    operation,
    value_type,
)
from marivo.analysis.model.disclosure import (
    ParameterInput as P,
)
from marivo.analysis.model.method_targets import FrameMethodOutput, FrameMethodTarget
from marivo.analysis.model.method_values import FrameMethodValue

_FACT_TYPES: tuple[tuple[type[object], str, str, tuple[str, ...]], ...] = (
    (
        FrameMethodFacts,
        "frame_method_facts",
        "Read a fixed Frame's method_facts tuple; each item explains its origin_result_id.",
        ("frame_method_parent", "frame_method_coordinate", "frame_method_target"),
    ),
    (
        FrameMethodParent,
        "frame_method_parent",
        "Read facts.parents in complete original parent-key order, including parents with no fine rows.",
        ("frame_method_value", "frame_attribute_metric"),
    ),
    (
        FrameAttributeMetric,
        "frame_attribute_metric",
        "Read parent.metrics in original target declaration order; target retains its complete output path and kind distinguishes level, absolute and relative.",
        (
            "frame_additive_method",
            "frame_ratio_method",
            "frame_shapley_method",
            "frame_linear_method",
            "frame_division_method",
            "frame_level_additive_method",
            "frame_level_ratio_method",
            "frame_level_linear_method",
            "frame_level_division_method",
            "frame_method_output",
        ),
    ),
    (
        FrameMethodValue,
        "frame_method_value",
        "Read a parent key or method total/component value; column owns its exact physical type and unit.",
        (),
    ),
    (
        FrameMethodCoordinate,
        "frame_method_coordinate",
        "Read facts.coordinates for complete independent original frozen interval/calendar/paired snapshots.",
        (),
    ),
    (
        FrameMethodSupport,
        "frame_method_support",
        "Read a two-endpoint method.support for local root/sample policy and exact current/baseline input/effective counts.",
        (),
    ),
    (
        FrameAdditiveMethod,
        "frame_additive_method",
        "Read metric.method or a linear term's method when kind is additive.",
        ("frame_method_value", "frame_method_support"),
    ),
    (
        FrameRatioMethod,
        "frame_ratio_method",
        "Read metric.method or a linear term's method when kind is ratio_component; components are parent totals.",
        ("frame_method_value", "frame_method_support"),
    ),
    (
        FrameShapleyMethod,
        "frame_shapley_method",
        "Read metric.method when kind is shapley for the fixed replacement game, sampling and capture costs.",
        (
            "frame_method_value",
            "frame_shapley_game",
            "frame_shapley_query_game",
            "frame_shapley_leaf",
            "frame_shapley_capture",
            "frame_shapley_query",
        ),
    ),
    (
        FrameShapleyGame,
        "frame_shapley_game",
        "Read shapley_method.game for inert formula identities, player semantics, domain proof and error policies.",
        (),
    ),
    (
        FrameShapleyQueryGame,
        "frame_shapley_query_game",
        "Read shapley_method.game for independent-read and evaluated-coalition policies; its marginal SE cannot isolate source changes.",
        (),
    ),
    (
        FrameShapleyLeaf,
        "frame_shapley_leaf",
        "Read shapley_method.leaves for original typed reducer occurrences, masks and capture references.",
        ("frame_shapley_reduction", "frame_shapley_quantile", "frame_shapley_native_quantile"),
    ),
    (
        FrameShapleyCapture,
        "frame_shapley_capture",
        "Read shapley_method.captures for actual statement/state costs; decoded bytes differ from wire and scanned bytes.",
        (),
    ),
    (
        FrameShapleyQuery,
        "frame_shapley_query",
        "Read shapley_method.captures for actual native-query receipts and costs; unknown scan/wire statistics remain unknown.",
        (),
    ),
    (
        FrameShapleyReduction,
        "frame_shapley_reduction",
        "Read shapley_leaf.reduction for a non-quantile original reducer identity.",
        (),
    ),
    (
        FrameShapleyQuantile,
        "frame_shapley_quantile",
        "Read shapley_leaf.reduction for the original exact median/quantile and q.",
        (),
    ),
    (
        FrameShapleyNativeQuantile,
        "frame_shapley_native_quantile",
        "Read shapley_leaf.reduction for the original exact/approximate native quantile reducer and q; no replacement algorithm is used.",
        (),
    ),
    (
        FrameLinearTerm,
        "frame_linear_term",
        "Read linear_method.terms in captured subexpression order; coefficient preserves its finite numeric type.",
        (
            "frame_additive_method",
            "frame_ratio_method",
            "frame_linear_method",
            "frame_division_method",
        ),
    ),
    (
        FrameLinearMethod,
        "frame_linear_method",
        "Read metric.method or a linear term's method when kind is linear.",
        ("frame_method_value", "frame_linear_term"),
    ),
)
_FACT_TYPES += (
    (
        FrameMethodTarget,
        "frame_method_target",
        "Read facts.targets for the complete target directory, including empty parent domains.",
        ("frame_method_output",),
    ),
    (
        FrameMethodOutput,
        "frame_method_output",
        "Read target.outputs for exact role-to-output paths.",
        (),
    ),
    (
        FrameLevelSupport,
        "frame_level_support",
        "Read one-endpoint method support counts and sample policy.",
        (),
    ),
    (
        FrameLevelAdditiveMethod,
        "frame_level_additive_method",
        "Read level target.method when kind is level_additive.",
        ("frame_method_value", "frame_level_support"),
    ),
    (
        FrameLevelRatioMethod,
        "frame_level_ratio_method",
        "Read level target.method when kind is level_ratio_component.",
        ("frame_method_value", "frame_level_support"),
    ),
    (
        FrameLevelLinearTerm,
        "frame_level_linear_term",
        "Read level linear method terms in original order.",
        (
            "frame_level_additive_method",
            "frame_level_ratio_method",
            "frame_level_linear_method",
            "frame_level_division_method",
        ),
    ),
    (
        FrameLevelLinearMethod,
        "frame_level_linear_method",
        "Read level target.method when kind is level_linear.",
        ("frame_method_value", "frame_level_linear_term"),
    ),
)
_FACT_TYPES += (
    (
        FrameDivisionMethod,
        "frame_division_method",
        "Read a paired division method's original typed divisor, operation and child method; result columns retain the declared units.",
        (
            "frame_method_value",
            "frame_additive_method",
            "frame_ratio_method",
            "frame_linear_method",
            "frame_division_method",
        ),
    ),
    (
        FrameLevelDivisionMethod,
        "frame_level_division_method",
        "Read a level division method's original typed divisor, operation and child method; result columns retain the declared units.",
        (
            "frame_method_value",
            "frame_level_additive_method",
            "frame_level_ratio_method",
            "frame_level_linear_method",
            "frame_level_division_method",
        ),
    ),
)
_FACT_CONSTRAINTS: dict[type[object], tuple[str, ...]] = {
    FrameMethodFacts: (
        "Inherited facts explain origin_result_id, not derived values; they grant no new computation authority.",
        "targets retains explicit role-to-output paths even when parents is empty.",
    ),
    FrameAttributeMetric: (
        "observed_total retains the receiver value; total is the fresh parent result. share_state is defined or zero_total.",
    ),
    FrameMethodValue: (
        "Values preserve their physical types, Decimal precision and integer time ticks.",
    ),
    FrameMethodSupport: ("Counts do not prove source qualification.",),
    FrameLevelSupport: ("Counts do not prove source qualification.",),
}


def add_method_fact_descriptors(descriptors: list[Descriptor]) -> None:
    for fact_owner, target, acquisition, nested in _FACT_TYPES:
        descriptors.append(
            value_type(
                target,
                fact_owner,
                summary=f"Immutable {fact_owner.__name__} original explanation facts.",
                acquisition=acquisition + " Direct construction and extension are unsupported.",
                producers=("analysis_frame.method_facts",)
                if fact_owner is FrameMethodFacts
                else {
                    FrameDivisionMethod: (
                        "frame_attribute_metric.method",
                        "frame_linear_term.method",
                        "frame_division_method.method",
                    ),
                    FrameLevelDivisionMethod: (
                        "frame_attribute_metric.method",
                        "frame_level_linear_term.method",
                        "frame_level_division_method.method",
                    ),
                    FrameMethodTarget: ("frame_method_facts.targets",),
                    FrameMethodOutput: (
                        "frame_method_target.outputs",
                        "frame_attribute_metric.outputs",
                    ),
                    FrameLevelSupport: (
                        "frame_level_additive_method.support",
                        "frame_level_ratio_method.support",
                    ),
                    FrameLevelAdditiveMethod: (
                        "frame_level_division_method.method",
                        "frame_attribute_metric.method",
                        "frame_level_linear_term.method",
                    ),
                    FrameLevelRatioMethod: (
                        "frame_level_division_method.method",
                        "frame_attribute_metric.method",
                        "frame_level_linear_term.method",
                    ),
                    FrameLevelLinearTerm: ("frame_level_linear_method.terms",),
                    FrameLevelLinearMethod: (
                        "frame_level_division_method.method",
                        "frame_attribute_metric.method",
                        "frame_level_linear_term.method",
                    ),
                    FrameMethodParent: ("frame_method_facts.parents",),
                    FrameAttributeMetric: ("frame_method_parent.metrics",),
                    FrameMethodValue: (
                        "frame_method_parent.key",
                        "frame_additive_method.left",
                        "frame_ratio_method.numerator_left",
                        "frame_ratio_method.denominator_left",
                        "frame_linear_method.left",
                    ),
                    FrameMethodCoordinate: ("frame_method_facts.coordinates",),
                    FrameMethodSupport: (
                        "frame_additive_method.support",
                        "frame_ratio_method.support",
                    ),
                    FrameAdditiveMethod: (
                        "frame_division_method.method",
                        "frame_attribute_metric.method",
                        "frame_linear_term.method",
                    ),
                    FrameRatioMethod: (
                        "frame_attribute_metric.method",
                        "frame_linear_term.method",
                        "frame_division_method.method",
                    ),
                    FrameShapleyMethod: ("frame_attribute_metric.method",),
                    FrameShapleyGame: ("frame_shapley_method.game",),
                    FrameShapleyQueryGame: ("frame_shapley_method.game",),
                    FrameShapleyLeaf: ("frame_shapley_method.leaves",),
                    FrameShapleyCapture: ("frame_shapley_method.captures",),
                    FrameShapleyQuery: ("frame_shapley_method.captures",),
                    FrameShapleyReduction: ("frame_shapley_leaf.reduction",),
                    FrameShapleyQuantile: ("frame_shapley_leaf.reduction",),
                    FrameShapleyNativeQuantile: ("frame_shapley_leaf.reduction",),
                    FrameLinearMethod: (
                        "frame_division_method.method",
                        "frame_attribute_metric.method",
                        "frame_linear_term.method",
                    ),
                    FrameLinearTerm: ("frame_linear_method.terms",),
                }[fact_owner],
                consumers=("frame_method_facts.show", *nested),
                constraints=(
                    "Read-only facts; bounded show does not truncate typed fields.",
                    *_FACT_CONSTRAINTS.get(fact_owner, ()),
                ),
            )
        )
    descriptors.append(
        operation(
            "frame_method_facts.show",
            "facts.show",
            FrameMethodFacts.show,
            bindings=tuple(bind(FrameMethodFacts.show, owner) for owner, _, _, _ in _FACT_TYPES),
            summary="Print a bounded view of any concrete Frame method-fact record already in memory.",
            parameters=(
                P(
                    "n",
                    "Exact nonnegative int excluding bool; default 20; zero prints fields and counts only.",
                ),
            ),
            output="None",
            constraints=(
                "Shared by FrameMethodFacts and its concrete nested record types. At most n detail lines; no field or record truncation in the immutable carrier.",
                "No file/source/model reads. Obtaining stored_frame.method_facts beforehand is a separate full method-payload read, even when this show uses n=0.",
            ),
            effects="Print already-owned immutable facts only.",
            failures=("AnalysisError: provide a nonnegative exact integer n, excluding bool.",),
            example=ExampleInput("result = facts.show(n=0)", ("facts",), "result", "None"),
            related=("frame_method_facts",),
        )
    )
