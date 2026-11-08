"""Native disclosure for explicit plain-table persistence and fixed references."""

from marivo.analysis._capabilities.dataset_model import (
    Descriptor,
    DisclosureProvider,
    ExampleInput,
    ExportInput,
    bind,
    operation,
    value_type,
)
from marivo.analysis._capabilities.dataset_model import ParameterInput as P
from marivo.analysis.core.result_reference import DataFrameReference, FrameReference
from marivo.analysis.results import persist_dataframe


def provider() -> DisclosureProvider:
    descriptors: list[Descriptor] = [
        operation(
            "persist_dataframe",
            "mv.persist_dataframe",
            persist_dataframe,
            summary="Explicitly save one immutable pandas table without an Analysis Session.",
            parameters=(
                P("frame", "Table with default unnamed RangeIndex and supported scalar columns."),
                P("project_root", "Exact existing destination; omission discovers the project."),
            ),
            output="DataFrameReference",
            constraints=(
                "No implicit source reads, semantic metadata, attrs or HTTP server. Each call publishes a new version.",
            ),
            effects="Write one complete Parquet snapshot under the project .marivo directory.",
            failures=("AnalysisError: repair the table structure or destination permissions.",),
            example=ExampleInput(
                "import pandas as pd\nimport marivo.analysis as mv\nresult = mv.persist_dataframe(pd.DataFrame({'orders': [12, 7]}))",
                (),
                "result",
                "DataFrameReference",
            ),
            discovery_group="execution",
        )
    ]
    for owner, target, producer in (
        (FrameReference, "frame_reference", "analysis_frame.ref"),
        (DataFrameReference, "dataframe_reference", "persist_dataframe"),
    ):
        descriptors.append(
            value_type(
                target,
                owner,
                summary="Immutable project-bound fixed result reference.",
                acquisition=f"Use {producer}; direct construction and extension are unsupported.",
                producers=(producer,),
                consumers=(target + ".to_dict", "results.service.http"),
                constraints=(
                    "Identity only; no service URL, credentials, report or producer replay.",
                ),
            )
        )
        descriptors.append(
            operation(
                target + ".to_dict",
                "reference.to_dict",
                owner.to_dict,
                bindings=(bind(owner.to_dict, owner),),
                summary="Export a fresh concrete wire reference.",
                parameters=(),
                output="a typed identity dictionary",
                constraints=("No I/O; modifying the dictionary cannot modify the reference.",),
                effects="Copy captured identity fields.",
                failures=(),
                example=ExampleInput(
                    "result = reference.to_dict()", ("reference",), "result", "dict"
                ),
            )
        )
    return DisclosureProvider(
        "analysis.results",
        tuple(descriptors),
        (
            ExportInput("persist_dataframe", persist_dataframe, "persist_dataframe"),
            ExportInput("FrameReference", FrameReference, "frame_reference"),
            ExportInput("DataFrameReference", DataFrameReference, "dataframe_reference"),
        ),
    )
