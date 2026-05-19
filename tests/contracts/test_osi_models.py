"""Tests for OSI core Pydantic models and MARIVO extension parsing."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

# -- OSI Core Model Tests --


def test_expression_single_dialect():
    from marivo.contracts.generated import DialectExpression, Expression

    expr = Expression(
        dialects=[DialectExpression(dialect="ANSI_SQL", expression="ss_sold_date_sk")]
    )
    assert len(expr.dialects) == 1
    assert expr.dialects[0].dialect == "ANSI_SQL"
    assert expr.dialects[0].expression == "ss_sold_date_sk"


def test_expression_accepts_trino_dialect():
    from marivo.contracts.generated import DialectExpression, Expression

    expr = Expression(
        dialects=[
            DialectExpression(
                dialect="TRINO",
                expression="date_parse(log_date, '%Y%m%d')",
            )
        ]
    )

    assert expr.dialects[0].dialect == "TRINO"


def test_expression_requires_at_least_one_dialect():
    from marivo.contracts.generated import Expression

    with pytest.raises(ValidationError):
        Expression(dialects=[])


def test_ai_context_string_form():
    from marivo.contracts.generated import AIContext

    ctx = AIContext(root="Use this model for retail analytics")
    assert ctx.root == "Use this model for retail analytics"


def test_ai_context_object_form():
    from marivo.contracts.generated import AIContext, AIContextObject

    ctx = AIContext(
        root={
            "instructions": "Use this for retail",
            "synonyms": ["retail", "store sales"],
            "examples": ["Show me sales by region"],
        }
    )
    assert isinstance(ctx.root, AIContextObject)
    assert ctx.root.instructions == "Use this for retail"


def test_field_minimal():
    from marivo.contracts.generated import DialectExpression, Expression, Field

    field = Field(
        name="ss_sold_date_sk",
        expression=Expression(
            dialects=[DialectExpression(dialect="ANSI_SQL", expression="ss_sold_date_sk")]
        ),
    )
    assert field.name == "ss_sold_date_sk"
    assert field.dimension is None
    assert field.custom_extensions is None


def test_field_with_dimension_is_time():
    from marivo.contracts.generated import DialectExpression, Dimension, Expression, Field
    from marivo.contracts.generated.osi import MarivoFieldCustomExtension, MarivoFieldExtension

    field = Field(
        name="ss_sold_time",
        expression=Expression(
            dialects=[DialectExpression(dialect="ANSI_SQL", expression="ss_sold_time_sk")]
        ),
        dimension=Dimension(is_time=True),
        custom_extensions=[
            MarivoFieldCustomExtension(
                vendor_name="MARIVO",
                data=MarivoFieldExtension(support_min_granularity="hour", data_type="date"),
            )
        ],
    )
    assert field.dimension is not None
    assert field.dimension.is_time is True


def test_dataset_minimal():
    from marivo.contracts.generated import Dataset

    ds = Dataset(name="store_sales", source="tpcds.public.store_sales")
    assert ds.name == "store_sales"
    assert ds.fields is None


def test_relationship_requires_columns():
    from marivo.contracts.generated import Relationship

    rel = Relationship(
        name="store_sales_to_date",
        **{"from": "store_sales"},
        to="date_dim",
        from_columns=["ss_sold_date_sk"],
        to_columns=["d_date_sk"],
    )
    assert rel.from_ == "store_sales"


def test_metric_minimal():
    from marivo.contracts.generated import DialectExpression, Expression, Metric

    metric = Metric(
        name="total_sales",
        expression=Expression(
            dialects=[DialectExpression(dialect="ANSI_SQL", expression="SUM(ss_ext_sales_price)")]
        ),
    )
    assert metric.name == "total_sales"


def test_semantic_model_requires_datasets():
    from marivo.contracts.generated import SemanticModel

    with pytest.raises(ValidationError):
        SemanticModel(name="retail", datasets=[])


def test_osi_document_structure():
    from marivo.contracts.generated import OSIDocument

    doc = OSIDocument(version="0.1.1", semantic_model=[])
    assert doc.version == "0.1.1"


def test_osi_document_accepts_trino_top_level_dialect():
    from marivo.contracts.generated import OSIDocument

    doc = OSIDocument(version="0.1.1", dialects=["TRINO"], semantic_model=[])

    assert doc.dialects == ["TRINO"]


def test_osi_document_version_must_be_011():
    from marivo.contracts.generated import OSIDocument

    with pytest.raises(ValidationError):
        OSIDocument(version="0.2.0", semantic_model=[])


def test_custom_extension_structure():
    from marivo.contracts.generated import CustomExtension

    ext = CustomExtension(vendor_name="MARIVO", data={"datasource_id": "ds_001"})
    assert ext.vendor_name == "MARIVO"
    assert ext.data == {"datasource_id": "ds_001"}

    with pytest.raises(ValidationError):
        CustomExtension(vendor_name="COMMON", data={})


def test_field_forbids_extra_properties():
    from marivo.contracts.generated import DialectExpression, Expression, Field

    with pytest.raises(ValidationError):
        Field(
            name="x",
            expression=Expression(dialects=[DialectExpression(dialect="ANSI_SQL", expression="x")]),
            unknown_prop="bad",
        )


def test_unextended_objects_reject_custom_extensions():
    from marivo.contracts.generated import (
        CustomExtension,
        DialectExpression,
        Expression,
        Field,
        Relationship,
        SemanticModel,
    )

    extension = CustomExtension(vendor_name="MARIVO", data={"datasource_id": "ds_001"})
    expression = Expression(dialects=[DialectExpression(dialect="ANSI_SQL", expression="order_id")])

    with pytest.raises(ValidationError):
        Field(name="order_id", expression=expression, custom_extensions=[extension])

    with pytest.raises(ValidationError):
        Relationship(
            name="orders_to_customers",
            from_="orders",
            to="customers",
            from_columns=["customer_id"],
            to_columns=["customer_id"],
            custom_extensions=[extension],
        )

    with pytest.raises(ValidationError):
        SemanticModel(
            name="commerce",
            datasets=[
                {
                    "name": "orders",
                    "source": "analytics.orders",
                    "fields": [{"name": "order_id", "expression": expression}],
                }
            ],
            custom_extensions=[extension],
        )


# -- MARIVO Extension tests --


def test_marivo_semantic_model_extension_public():
    from marivo.transports.http.models.marivo_extensions import MarivoSemanticModelExtension

    ext = MarivoSemanticModelExtension(visibility="public")
    assert ext.visibility == "public"
    assert ext.owner_user is None


def test_marivo_semantic_model_extension_private_requires_owner():
    from marivo.transports.http.models.marivo_extensions import MarivoSemanticModelExtension

    with pytest.raises(ValidationError):
        MarivoSemanticModelExtension(visibility="private")


def test_marivo_semantic_model_extension_private_with_owner():
    from marivo.transports.http.models.marivo_extensions import MarivoSemanticModelExtension

    ext = MarivoSemanticModelExtension(visibility="private", owner_user="alice")
    assert ext.owner_user == "alice"


def test_marivo_dataset_extension():
    from marivo.transports.http.models.marivo_extensions import MarivoDatasetExtension

    ext = MarivoDatasetExtension(datasource_id="tpcds")
    assert ext.datasource_id == "tpcds"


def test_marivo_field_extension():
    from marivo.transports.http.models.marivo_extensions import MarivoFieldExtension

    ext = MarivoFieldExtension(support_min_granularity="day", data_type="date")
    assert ext.support_min_granularity == "day"


def test_time_field_accepts_marivo_support_min_granularity_extension():
    from marivo.contracts.generated import DialectExpression, Dimension, Expression, Field
    from marivo.contracts.generated.osi import (
        MarivoFieldCustomExtension,
        MarivoFieldExtension,
    )

    field = Field(
        name="log_date",
        expression=Expression(
            dialects=[DialectExpression(dialect="ANSI_SQL", expression="log_date")]
        ),
        dimension=Dimension(is_time=True),
        custom_extensions=[
            MarivoFieldCustomExtension(
                vendor_name="MARIVO",
                data=MarivoFieldExtension(support_min_granularity="day", data_type="date"),
            )
        ],
    )

    assert field.custom_extensions is not None
    assert field.custom_extensions[0].data.support_min_granularity == "day"


def test_time_field_requires_marivo_support_min_granularity_extension():
    from marivo.contracts.generated import DialectExpression, Dimension, Expression, Field

    with pytest.raises(ValidationError, match="time fields must define exactly one"):
        Field(
            name="log_date",
            expression=Expression(
                dialects=[DialectExpression(dialect="ANSI_SQL", expression="log_date")]
            ),
            dimension=Dimension(is_time=True),
        )


def test_non_time_field_rejects_marivo_field_extension():
    from marivo.contracts.generated import DialectExpression, Expression, Field
    from marivo.contracts.generated.osi import (
        MarivoFieldCustomExtension,
        MarivoFieldExtension,
    )

    with pytest.raises(ValidationError, match="non-time fields must not define"):
        Field(
            name="country",
            expression=Expression(
                dialects=[DialectExpression(dialect="ANSI_SQL", expression="country")]
            ),
            custom_extensions=[
                MarivoFieldCustomExtension(
                    vendor_name="MARIVO",
                    data=MarivoFieldExtension(support_min_granularity="day", data_type="date"),
                )
            ],
        )


def test_field_extension_rejects_invalid_support_min_granularity():
    from marivo.contracts.generated.osi import MarivoFieldExtension

    with pytest.raises(ValidationError):
        MarivoFieldExtension(support_min_granularity="minute", data_type="date")


def test_marivo_relationship_extension():
    from marivo.transports.http.models.marivo_extensions import MarivoRelationshipExtension

    ext = MarivoRelationshipExtension(cardinality="many_to_one")
    assert ext.cardinality == "many_to_one"


def test_marivo_metric_extension_minimal():
    from marivo.transports.http.models.marivo_extensions import MarivoMetricExtension

    ext = MarivoMetricExtension()
    assert ext.additive_dimensions == []


def test_marivo_metric_extension_all_dimensions():
    from marivo.transports.http.models.marivo_extensions import MarivoMetricExtension

    ext = MarivoMetricExtension(additive_dimensions=["__all"])
    assert ext.additive_dimensions == ["__all"]


def test_marivo_metric_filter():
    from marivo.transports.http.models.marivo_extensions import MarivoMetricFilter

    f = MarivoMetricFilter(
        name="active_only",
        expression={"dialects": [{"dialect": "ANSI_SQL", "expression": "is_active = 1"}]},
    )
    assert f.name == "active_only"


# -- Extension parsing tests --


def test_extract_marivo_extension_from_custom_extensions():
    from marivo.contracts.generated import CustomExtension
    from marivo.core.semantic.extensions import extract_marivo_extension
    from marivo.transports.http.models.marivo_extensions import MarivoDatasetExtension

    exts = [CustomExtension(vendor_name="MARIVO", data={"datasource_id": "tpcds"})]
    result = extract_marivo_extension(exts, MarivoDatasetExtension)
    assert result is not None
    assert result.datasource_id == "tpcds"


def test_extract_marivo_extension_returns_none_when_absent():
    from marivo.core.semantic.extensions import extract_marivo_extension
    from marivo.transports.http.models.marivo_extensions import MarivoSemanticModelExtension

    result = extract_marivo_extension([], MarivoSemanticModelExtension)
    assert result is None


def test_extract_marivo_extension_returns_none_for_empty():
    from marivo.core.semantic.extensions import extract_marivo_extension
    from marivo.transports.http.models.marivo_extensions import MarivoSemanticModelExtension

    result = extract_marivo_extension(None, MarivoSemanticModelExtension)
    assert result is None


def test_build_custom_extensions_with_marivo():
    from marivo.runtime.semantic.osi_storage import build_custom_extensions
    from marivo.transports.http.models.marivo_extensions import MarivoDatasetExtension

    exts = build_custom_extensions(MarivoDatasetExtension(datasource_id="tpcds"))
    assert len(exts) == 1
    assert exts[0].vendor_name == "MARIVO"
    assert exts[0].data.model_dump() == {"datasource_id": "tpcds"}


def test_build_custom_extensions_with_marivo_only():
    from marivo.runtime.semantic.osi_storage import build_custom_extensions
    from marivo.transports.http.models.marivo_extensions import MarivoDatasetExtension

    exts = build_custom_extensions(MarivoDatasetExtension(datasource_id="tpcds"))
    assert len(exts) == 1
    assert exts[0].vendor_name == "MARIVO"
