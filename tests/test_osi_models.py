"""Tests for OSI core Pydantic models and MARIVO extension parsing."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

# -- OSI Core Model Tests --


def test_expression_single_dialect():
    from marivo.transports.http.models.osi import DialectExpression, Expression

    expr = Expression(
        dialects=[DialectExpression(dialect="ANSI_SQL", expression="ss_sold_date_sk")]
    )
    assert len(expr.dialects) == 1
    assert expr.dialects[0].dialect == "ANSI_SQL"
    assert expr.dialects[0].expression == "ss_sold_date_sk"


def test_expression_requires_at_least_one_dialect():
    from marivo.transports.http.models.osi import Expression

    with pytest.raises(ValidationError):
        Expression(dialects=[])


def test_ai_context_string_form():
    from marivo.transports.http.models.osi import AIContext

    ctx = AIContext(root="Use this model for retail analytics")
    assert ctx.root == "Use this model for retail analytics"


def test_ai_context_object_form():
    from marivo.transports.http.models.osi import AIContext, AIContextObject

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
    from marivo.transports.http.models.osi import DialectExpression, Expression, Field

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
    from marivo.transports.http.models.osi import DialectExpression, Dimension, Expression, Field

    field = Field(
        name="ss_sold_time",
        expression=Expression(
            dialects=[DialectExpression(dialect="ANSI_SQL", expression="ss_sold_time_sk")]
        ),
        dimension=Dimension(is_time=True),
    )
    assert field.dimension is not None
    assert field.dimension.is_time is True


def test_dataset_minimal():
    from marivo.transports.http.models.osi import Dataset

    ds = Dataset(name="store_sales", source="tpcds.public.store_sales")
    assert ds.name == "store_sales"
    assert ds.fields is None


def test_relationship_requires_columns():
    from marivo.transports.http.models.osi import Relationship

    rel = Relationship(
        name="store_sales_to_date",
        **{"from": "store_sales"},
        to="date_dim",
        from_columns=["ss_sold_date_sk"],
        to_columns=["d_date_sk"],
    )
    assert rel.from_ == "store_sales"


def test_metric_minimal():
    from marivo.transports.http.models.osi import DialectExpression, Expression, Metric

    metric = Metric(
        name="total_sales",
        expression=Expression(
            dialects=[DialectExpression(dialect="ANSI_SQL", expression="SUM(ss_ext_sales_price)")]
        ),
    )
    assert metric.name == "total_sales"


def test_semantic_model_requires_datasets():
    from marivo.transports.http.models.osi import SemanticModel

    with pytest.raises(ValidationError):
        SemanticModel(name="retail", datasets=[])


def test_osi_document_structure():
    from marivo.transports.http.models.osi import OSIDocument

    doc = OSIDocument(version="0.1.1", semantic_model=[])
    assert doc.version == "0.1.1"


def test_osi_document_version_must_be_011():
    from marivo.transports.http.models.osi import OSIDocument

    with pytest.raises(ValidationError):
        OSIDocument(version="0.2.0", semantic_model=[])


def test_custom_extension_structure():
    from marivo.transports.http.models.osi import CustomExtension

    ext = CustomExtension(vendor_name="MARIVO", data='{"visibility": "public"}')
    assert ext.vendor_name == "MARIVO"
    assert ext.data == '{"visibility": "public"}'


def test_field_forbids_extra_properties():
    from marivo.transports.http.models.osi import DialectExpression, Expression, Field

    with pytest.raises(ValidationError):
        Field(
            name="x",
            expression=Expression(dialects=[DialectExpression(dialect="ANSI_SQL", expression="x")]),
            unknown_prop="bad",
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

    ext = MarivoFieldExtension(data_type="integer")
    assert ext.data_type == "integer"


def test_marivo_relationship_extension():
    from marivo.transports.http.models.marivo_extensions import MarivoRelationshipExtension

    ext = MarivoRelationshipExtension(cardinality="many_to_one")
    assert ext.cardinality == "many_to_one"


def test_marivo_metric_extension_minimal():
    from marivo.transports.http.models.marivo_extensions import MarivoMetricExtension

    ext = MarivoMetricExtension()
    assert ext.additive_dimensions is None


def test_marivo_additivity_full():
    from marivo.transports.http.models.marivo_extensions import MarivoAdditivity

    add = MarivoAdditivity(dimension_policy="all", time_axis_policy="additive")
    assert add.additive_dimensions is None


def test_marivo_additivity_subset_requires_dimensions():
    from marivo.transports.http.models.marivo_extensions import MarivoAdditivity

    with pytest.raises(ValidationError):
        MarivoAdditivity(dimension_policy="subset", time_axis_policy="additive")


def test_marivo_additivity_subset_with_dimensions():
    from marivo.transports.http.models.marivo_extensions import MarivoAdditivity

    add = MarivoAdditivity(
        dimension_policy="subset",
        time_axis_policy="additive",
        additive_dimensions=["region", "category"],
    )
    assert add.additive_dimensions == ["region", "category"]


def test_marivo_metric_filter():
    from marivo.transports.http.models.marivo_extensions import MarivoMetricFilter

    f = MarivoMetricFilter(
        name="active_only",
        expression={"dialects": [{"dialect": "ANSI_SQL", "expression": "is_active = 1"}]},
    )
    assert f.name == "active_only"


# -- Extension parsing tests --


def test_extract_marivo_extension_from_custom_extensions():
    from marivo.core.semantic.extensions import extract_marivo_extension
    from marivo.transports.http.models.marivo_extensions import MarivoSemanticModelExtension
    from marivo.transports.http.models.osi import CustomExtension

    exts = [CustomExtension(vendor_name="MARIVO", data='{"visibility": "public"}')]
    result = extract_marivo_extension(exts, MarivoSemanticModelExtension)
    assert result is not None
    assert result.visibility == "public"


def test_extract_marivo_extension_returns_none_when_absent():
    from marivo.core.semantic.extensions import extract_marivo_extension
    from marivo.transports.http.models.marivo_extensions import MarivoSemanticModelExtension
    from marivo.transports.http.models.osi import CustomExtension

    exts = [CustomExtension(vendor_name="COMMON", data="{}")]
    result = extract_marivo_extension(exts, MarivoSemanticModelExtension)
    assert result is None


def test_extract_marivo_extension_returns_none_for_empty():
    from marivo.core.semantic.extensions import extract_marivo_extension
    from marivo.transports.http.models.marivo_extensions import MarivoSemanticModelExtension

    result = extract_marivo_extension(None, MarivoSemanticModelExtension)
    assert result is None


def test_build_custom_extensions_with_marivo():
    from marivo.runtime.semantic.osi_storage import build_custom_extensions
    from marivo.transports.http.models.marivo_extensions import MarivoSemanticModelExtension

    exts = build_custom_extensions(MarivoSemanticModelExtension(visibility="public"))
    assert len(exts) == 1
    assert exts[0].vendor_name == "MARIVO"
    parsed = json.loads(exts[0].data)
    assert parsed["visibility"] == "public"


def test_build_custom_extensions_with_marivo_and_others():
    from marivo.runtime.semantic.osi_storage import build_custom_extensions
    from marivo.transports.http.models.marivo_extensions import MarivoSemanticModelExtension
    from marivo.transports.http.models.osi import CustomExtension

    other = CustomExtension(vendor_name="COMMON", data='{"note": "test"}')
    exts = build_custom_extensions(MarivoSemanticModelExtension(visibility="public"), other)
    assert len(exts) == 2
