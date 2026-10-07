"""Closed physical placements for canonical Journey assignment."""

from dataclasses import replace

from marivo.analysis.methods.domain_preparation import implementations as preparations
from marivo.analysis.methods.domain_preparation import (
    remote_implementations,
    sqlite_implementations,
)
from marivo.analysis.methods.physical import FixedShape, Implementation, NoTime, Qualified
from marivo.analysis.methods.semantics import MethodKey


def implementations(method: MethodKey) -> tuple[Implementation, ...]:
    from marivo.analysis.methods.physical import ScalarType

    bases = tuple(
        item
        for item in (
            preparations(MethodKey("occurrence.prepare"))
            + (
                (
                    sqlite_implementations(MethodKey("occurrence.prepare"))
                    + remote_implementations(MethodKey("occurrence.prepare"))
                )
                if method.name == "journey.match"
                else ()
            )
        )
        if item.key.input_types == (ScalarType("int64"),)
    )
    fixed = next(item for item in bases if isinstance(item.key.shape, FixedShape))
    bases = (*bases, replace(fixed, key=replace(fixed.key, shape=FixedShape(NoTime()))))
    return tuple(
        replace(
            item,
            key=replace(
                item.key,
                method=method,
                input_domains=("occurrence" if method.name == "journey.match" else "journey",),
                route="artifact_python"
                if isinstance(item.key.shape, FixedShape)
                else "ibis_python",
            ),
            parts=("subject", "occurrences", "journey"),
            numeric_specialization="consumer",
            qualification=Qualified(
                f"r73.{method.name}.{item.key.shape}@v1",
                "analysis.materialization.journey_execution"
                if method.name == "journey.match"
                else "analysis.materialization.journey_views",
                "tests/test_analysis_journey_matching_r73.py",
            ),
        )
        for item in bases
    )


def consumers(method: MethodKey) -> tuple[Implementation, ...]:
    """Qualify retained Journey selection, Subject image and current-row reducers."""
    from marivo.analysis.methods.physical import DurationType, ScalarType

    if method.name in ("row.count", "row.count_defined"):
        return tuple(
            replace(
                item,
                key=replace(item.key, method=method, input_types=(value,)),
                parts=("row_state",),
                precision="checked_int64",
                numeric_specialization="consumer",
                qualification=Qualified(
                    f"r94.{method.name}.{value}.{item.key.shape}@v1",
                    "analysis.materialization.graph_local_execution",
                    "tests/test_r94_archived_domain_recovery.py",
                ),
            )
            for item in implementations(MethodKey("journey.read"))
            if item.key.shape == FixedShape(NoTime())
            for value in (ScalarType("string"), ScalarType("timestamp"), DurationType("us"))
        )
    if method.name not in ("map_correspond", "parts_transport", "row.mean", "domain.cohort"):
        return ()
    values = (
        (DurationType("us"),)
        if method.name == "row.mean"
        else (
            ScalarType("int64"),
            ScalarType("boolean"),
            ScalarType("string"),
            ScalarType("timestamp"),
            DurationType("us"),
        )
    )
    return tuple(
        replace(
            item,
            key=replace(
                item.key,
                method=method,
                input_types=(subject_type, value) if method.name == "domain.cohort" else (value,),
                input_domains=("entity", "journey")
                if method.name == "domain.cohort"
                else (domain,),
            ),
            checks=tuple(
                dict.fromkeys(
                    (
                        *item.checks,
                        *(
                            ("source.finite_numeric@v1",)
                            if method.name == "row.mean"
                            else ("source.group_mapping@v1",)
                            if method.name == "map_correspond"
                            else ()
                        ),
                    )
                )
            ),
            precision="checked_int64" if method.name == "row.mean" else "exact",
            parts=("row_state",)
            if method.name == "row.mean"
            else ("subject", "journey", "cohort_decision"),
            numeric_specialization="consumer",
            qualification=Qualified(
                f"r73.{method.name}.{item.key.shape}@v1",
                "analysis.materialization.graph_local_execution",
                "tests/test_analysis_journey_matching_r73.py",
            ),
        )
        for item in implementations(MethodKey("journey.read"))
        for value in values
        for subject_type in (
            (ScalarType("int64"), ScalarType("string"))
            if method.name == "domain.cohort"
            else (ScalarType("int64"),)
        )
        for domain in (
            ("journey", "group", "singleton")
            if method.name == "row.mean" and isinstance(item.key.shape, FixedShape)
            else ("journey",)
        )
    )
