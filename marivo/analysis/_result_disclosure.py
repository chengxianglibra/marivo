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
from marivo.analysis.core.dataframe_result import DataFrameResult
from marivo.analysis.core.result_reference import DataFrameReference, FrameReference
from marivo.analysis.results import persist_dataframe, read_dataframe


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
                "The reference captures the resolved absolute project_root and exact result_id for subsequent local reads.",
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
            related=("read_dataframe",),
        ),
        operation(
            "read_dataframe",
            "mv.read_dataframe",
            read_dataframe,
            summary="Read a complete fixed pandas snapshot by its exact saved reference.",
            parameters=(
                P(
                    "reference",
                    "Saved DataFrameReference or its original three-field to_dict() dictionary with captured absolute project_root and result_id.",
                ),
            ),
            output="DataFrameResult",
            constraints=(
                "Complete local read or AnalysisError; memory grows with the full result. No HTTP limits or pagination.",
                "Use only the captured local project path; no current-directory discovery or location override. The path must be accessible on this filesystem.",
                "No Session, Catalog, sources, HTTP server, state creation or producer replay.",
                "Reject bare IDs, paths, JSON strings, extra reference fields and AnalysisFrame references.",
            ),
            effects="Read and validate one saved snapshot; close its file before returning detached data and metadata.",
            failures=(
                "AnalysisError: check the exact reference, project, file integrity and read permissions; follow repair.",
            ),
            example=ExampleInput(
                "import pandas as pd\nimport marivo.analysis as mv\nreference = mv.persist_dataframe(pd.DataFrame({'orders': [12, 7]}))\nresult = mv.read_dataframe(reference.to_dict())\nresult.show()",
                (),
                "result",
                "DataFrameResult",
            ),
            discovery_group="results",
            related=("dataframe_result", "dataframe_result.to_pandas", "persist_dataframe"),
        ),
        value_type(
            "dataframe_result",
            DataFrameResult,
            summary="Complete immutable plain-table values and saved identity, UTC commit time, row count and Arrow schema.",
            acquisition="Use mv.read_dataframe(reference); direct construction is unsupported.",
            producers=("read_dataframe",),
            consumers=("dataframe_result.to_pandas",),
            constraints=(
                "No retained files or implicit reads. schema contains ordered physical types without internal metadata; row_count includes zero-column rows.",
                "Commit time is save time, not business freshness. No inferred units, lineage, periods or source metadata.",
            ),
        ),
        operation(
            "dataframe_result.to_pandas",
            "result.to_pandas",
            DataFrameResult.to_pandas,
            bindings=(bind(DataFrameResult.to_pandas, DataFrameResult),),
            summary="Export the complete table with isolated buffers and exact saved Arrow-backed types.",
            parameters=(),
            output="pandas.DataFrame",
            constraints=(
                "Unnamed default RangeIndex and empty attrs. Preserve saved order, nulls, large integers, Decimal precision and timestamp/duration units and timezone.",
                "Each export is independently mutable. Original pandas dtype containers and attrs are not reconstructed; convert explicitly for arithmetic.",
            ),
            effects="Copy complete captured data without I/O; memory grows with the complete table.",
            failures=(),
            example=ExampleInput(
                "table = result.to_pandas()", ("result",), "table", "pandas.DataFrame"
            ),
        ),
    ]
    for owner, target, producer in (
        (FrameReference, "frame_reference", "analysis_frame.ref"),
        (DataFrameReference, "dataframe_reference", "persist_dataframe"),
    ):
        descriptors.append(
            value_type(
                target,
                owner,
                summary="Immutable local table locator with captured absolute project_root and exact result_id."
                if owner is DataFrameReference
                else "Immutable project-bound fixed result reference.",
                acquisition=f"Use {producer}; direct construction and extension are unsupported.",
                producers=(producer,),
                consumers=(target + ".to_dict", "results.service.http")
                + (("read_dataframe",) if owner is DataFrameReference else ()),
                constraints=(
                    "Use read_dataframe(reference) on the captured local filesystem path. No path override, discovery fallback, credentials or producer replay."
                    if owner is DataFrameReference
                    else "Identity only; no service URL, credentials, report or producer replay.",
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
            ExportInput("read_dataframe", read_dataframe, "read_dataframe"),
            ExportInput("DataFrameResult", DataFrameResult, "dataframe_result"),
            ExportInput("FrameReference", FrameReference, "frame_reference"),
            ExportInput("DataFrameReference", DataFrameReference, "dataframe_reference"),
        ),
    )
