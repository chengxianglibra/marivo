"""Tests for the shared introspection internals."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field, field_validator

from marivo.introspection.constraints import ASTSpec, Constraint
from marivo.introspection.describe import (
    describe_object,
    own_doc,
    pydantic_fields,
    resolve_method_descriptor,
)
from marivo.introspection.render import render_json, render_text
from marivo.introspection.schema import Descriptor, FieldInfo, MethodInfo
from marivo.introspection.surface import Surface, render


class _BaseDoc:
    """Inherited doc that must not leak."""


class _OwnDoc(_BaseDoc):
    """Own doc line."""

    def sample(self, value: int) -> int:
        """Return the supplied value."""
        return value


class _PydanticNoDoc(BaseModel):
    value: int


@dataclass
class _FrameLike:
    """Frame-like class."""

    _NEXT_INTENTS = ("compare", "discover")

    def to_pandas(self) -> object:
        """Return a defensive copy."""
        return object()


class _BaseFrameLike:
    def to_pandas(self) -> object:
        """Return a defensive copy."""
        return object()


class _InheritedFrameLike(_BaseFrameLike):
    """Frame-like class with inherited frame API."""

    _NEXT_INTENTS = ("compare",)


def test_constraint_to_dict_accepts_plain_string_id() -> None:
    constraint = Constraint(
        id="example_rule",
        error_kind="example_error",
        phase="runtime",
        applies_to=("help",),
        title="Example rule.",
        why="Agents need stable rule metadata.",
        hint="Call help('example_rule') for details.",
        example="marivo/skills/marivo-analysis/references/examples/01_observe_single_window.py",
        docs_ref="marivo/skills/marivo-analysis/references/cheatsheet.md",
        ast_spec=ASTSpec(
            name="single_return",
            single_return=True,
            forbidden_statements=("Assign",),
            allowed_calls=("ms.ref",),
        ),
    )

    assert constraint.to_dict() == {
        "id": "example_rule",
        "error_kind": "example_error",
        "phase": "runtime",
        "applies_to": ["help"],
        "title": "Example rule.",
        "why": "Agents need stable rule metadata.",
        "hint": "Call help('example_rule') for details.",
        "example": "marivo/skills/marivo-analysis/references/examples/01_observe_single_window.py",
        "docs_ref": "marivo/skills/marivo-analysis/references/cheatsheet.md",
        "ast_spec": {
            "name": "single_return",
            "single_return": True,
            "forbidden_statements": ["Assign"],
            "forbidden_attributes": [],
            "forbidden_calls": [],
            "allowed_calls": ["ms.ref"],
            "allowed_binops": [],
            "allowed_unary_ops": [],
            "component_call_only": False,
            "shadowed_attributes": [],
        },
    }


def test_constraint_summary_is_l1_bounded() -> None:
    constraint = Constraint(
        id="summary_rule",
        error_kind="summary_error",
        phase="runtime",
        applies_to=("MetricFrame",),
        title="Summary rule.",
        why="This rationale is intentionally excluded from L1.",
        hint="Use the supported frame method.",
        example="marivo/skills/marivo-analysis/references/examples/compare_panel.py",
    )

    assert constraint.to_summary_dict() == {
        "id": "summary_rule",
        "title": "Summary rule.",
        "hint": "Use the supported frame method.",
        "example": "marivo/skills/marivo-analysis/references/examples/compare_panel.py",
    }


def test_own_doc_does_not_walk_mro_or_module_doc() -> None:
    assert own_doc(_OwnDoc) == "Own doc line."
    assert own_doc(_BaseDoc) == "Inherited doc that must not leak."
    assert own_doc(_PydanticNoDoc) == ""


def test_describe_class_lists_public_methods_as_l1_summaries() -> None:
    descriptor = describe_object(
        surface="test.surface",
        symbol="_OwnDoc",
        obj=_OwnDoc,
        summary="class summary",
        constraints=(),
        examples=(),
        see_also=("help('other')",),
    )

    assert descriptor.kind == "class"
    assert descriptor.signature == "class _OwnDoc"
    assert descriptor.doc == "Own doc line."
    assert descriptor.methods == (MethodInfo(name="sample", summary="Return the supplied value."),)
    assert render_json(descriptor)["methods"] == [
        {"name": "sample", "summary": "Return the supplied value."}
    ]


def test_signature_for_falls_back_when_annotation_eval_fails() -> None:
    namespace: dict[str, object] = {}
    exec(
        "from __future__ import annotations\n"
        "\n"
        "def broken(value: MissingType) -> MissingReturn:\n"
        "    return value\n",
        namespace,
    )

    descriptor = describe_object(
        surface="test.surface",
        symbol="broken",
        obj=namespace["broken"],
        summary="broken signature",
        constraints=(),
        examples=(),
        see_also=(),
    )

    assert descriptor.signature == "broken(...)"


def test_describe_frame_suppresses_dataclass_init_and_methods() -> None:
    descriptor = describe_object(
        surface="test.surface",
        symbol="_FrameLike",
        obj=_FrameLike,
        summary="frame summary",
        constraints=(),
        examples=(),
        see_also=(),
        frame_symbols={"_FrameLike"},
        constructed_by={"_FrameLike": "session.observe(...)"},
    )

    data = render_json(descriptor)
    assert data["kind"] == "frame"
    assert "signature" not in data
    assert data["constructed_by"] == "session.observe(...)"
    assert "next_intents" not in data
    assert data["methods"] == [{"name": "to_pandas", "summary": "Return a defensive copy."}]


def test_frame_descriptors_include_inherited_frame_methods() -> None:
    descriptor = describe_object(
        surface="test.surface",
        symbol="_InheritedFrameLike",
        obj=_InheritedFrameLike,
        summary="frame summary",
        constraints=(),
        examples=(),
        see_also=(),
        frame_symbols={"_InheritedFrameLike"},
        constructed_by={"_InheritedFrameLike": "session.observe(...)"},
    )

    data = render_json(descriptor)
    assert data["kind"] == "frame"
    assert data["methods"] == [{"name": "to_pandas", "summary": "Return a defensive copy."}]


def test_method_drilldown_returns_signature_and_own_doc() -> None:
    descriptor = resolve_method_descriptor(
        surface="test.surface",
        dotted_path="_OwnDoc.sample",
        owner=_OwnDoc,
        summary="Return the supplied value.",
    )

    data = render_json(descriptor)
    assert data["kind"] == "callable"
    assert data["symbol"] == "_OwnDoc.sample"
    assert data["signature"] == "_OwnDoc.sample(self, value: int) -> int"
    assert data["doc"] == "Return the supplied value."


def test_method_drilldown_rejects_inherited_public_methods() -> None:
    def resolve(symbol: str) -> object | None:
        return _PydanticNoDoc if symbol == "_PydanticNoDoc" else None

    surface = Surface(
        name="test.surface",
        all_names=("_PydanticNoDoc",),
        summaries={"_PydanticNoDoc": "pydantic model"},
        resolve=resolve,
        catalog={},
        topics={},
    )

    data = render(surface, "_PydanticNoDoc.model_dump", "json")
    assert isinstance(data, dict)
    assert data["kind"] == "unknown"


def test_method_drilldown_allows_inherited_frame_methods() -> None:
    def resolve(symbol: str) -> object | None:
        return _InheritedFrameLike if symbol == "_InheritedFrameLike" else None

    surface = Surface(
        name="test.surface",
        all_names=("_InheritedFrameLike",),
        summaries={"_InheritedFrameLike": "frame summary"},
        resolve=resolve,
        catalog={},
        topics={},
        frame_symbols={"_InheritedFrameLike"},
    )

    data = render(surface, "_InheritedFrameLike.to_pandas", "json")
    assert isinstance(data, dict)
    assert data["kind"] == "callable"
    assert data["symbol"] == "_InheritedFrameLike.to_pandas"
    assert data["doc"] == "Return a defensive copy."


def test_surface_topics_accept_prebuilt_descriptors() -> None:
    topic = Descriptor(
        surface="test.surface",
        kind="topic",
        symbol="topic",
        summary="prebuilt topic",
        content={"items": ["one"]},
    )
    surface = Surface(
        name="test.surface",
        all_names=("topic",),
        summaries={"topic": "prebuilt topic"},
        resolve=lambda symbol: None,
        catalog={},
        topics={"topic": topic},
    )

    data = render(surface, "topic", "json")
    assert isinstance(data, dict)
    assert data["kind"] == "topic"
    assert data["content"] == {"items": ["one"]}


def test_surface_render_handles_unknown_with_did_you_mean() -> None:
    def resolve(symbol: str) -> object | None:
        return _OwnDoc if symbol == "_OwnDoc" else None

    surface = Surface(
        name="test.surface",
        all_names=("_OwnDoc",),
        summaries={"_OwnDoc": "class summary"},
        resolve=resolve,
        catalog={},
        topics={},
    )

    data = render(surface, "_OwnDc", "json")
    assert isinstance(data, dict)
    assert data["kind"] == "unknown"
    assert data["did_you_mean"] == ["_OwnDoc"]
    assert "help()" in data["summary"]


def test_surface_top_level_entries_are_derived_from_all_names() -> None:
    def resolve(symbol: str) -> object | None:
        return _OwnDoc if symbol == "_OwnDoc" else None

    surface = Surface(
        name="test.surface",
        all_names=("_OwnDoc",),
        summaries={"_OwnDoc": "class summary"},
        resolve=resolve,
        catalog={},
        topics={},
    )

    data = render(surface, None, "json")
    assert isinstance(data, dict)
    assert data["kind"] == "surface"
    # A plain class with no callable kind folds into a family.
    assert data["entries"] == []
    assert data["families"] == [{"label": "Other types", "members": ["_OwnDoc"]}]
    assert "test.surface" in render(surface, None, "text")


class _PydanticWithFieldAndValidator(BaseModel):
    name: str = Field(description="item name")
    count: int = 0
    tag: str | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        return v.strip()


def test_pydantic_fields_extracts_field_metadata() -> None:
    fields = pydantic_fields(_PydanticWithFieldAndValidator)
    assert len(fields) == 3
    assert fields[0] == FieldInfo(
        name="name", annotation="str", required=True, default=None, description="item name"
    )
    assert fields[1] == FieldInfo(
        name="count", annotation="int", required=False, default="0", description=None
    )
    assert fields[2] == FieldInfo(
        name="tag", annotation="str | None", required=False, default="None", description=None
    )


def test_pydantic_fields_returns_empty_for_non_basemodel() -> None:
    assert pydantic_fields(_OwnDoc) == ()


def test_dataclass_field_infos_reads_metadata_description() -> None:
    from dataclasses import dataclass, field

    from marivo.introspection.describe import dataclass_field_infos

    @dataclass(frozen=True)
    class Sample:
        name: str = field(metadata={"description": "the name"})
        count: int = field(metadata={"description": "how many"})

    infos = dataclass_field_infos(Sample)
    assert [f.name for f in infos] == ["name", "count"]
    assert infos[0].annotation == "str"
    assert infos[0].required is True
    assert infos[0].default is None
    assert infos[0].description == "the name"
    assert infos[1].description == "how many"


def test_dataclass_field_infos_handles_missing_description_and_defaults() -> None:
    from dataclasses import dataclass, field

    from marivo.introspection.describe import dataclass_field_infos

    @dataclass(frozen=True)
    class Sample:
        flag: bool = field(default=False)

    infos = dataclass_field_infos(Sample)
    assert infos[0].name == "flag"
    assert infos[0].required is False
    assert infos[0].default == "False"
    assert infos[0].description is None


def test_dataclass_field_infos_empty_for_non_dataclass() -> None:
    from marivo.introspection.describe import dataclass_field_infos

    class Plain:
        pass

    assert dataclass_field_infos(Plain) == ()


def test_field_infos_falls_back_to_dataclass_fields() -> None:
    from dataclasses import dataclass, field

    from marivo.introspection.describe import field_infos

    @dataclass(frozen=True)
    class Sample:
        name: str = field(metadata={"description": "d"})

    infos = field_infos(Sample)
    assert [f.name for f in infos] == ["name"]
    assert infos[0].description == "d"


def test_field_infos_preserves_pydantic_fields() -> None:
    from marivo.introspection.describe import field_infos

    infos = field_infos(_PydanticWithFieldAndValidator)
    assert infos == pydantic_fields(_PydanticWithFieldAndValidator)
    assert [f.name for f in infos] == ["name", "count", "tag"]
    assert infos[0].description == "item name"


def test_pydantic_validators_filtered_from_public_methods() -> None:
    from marivo.introspection.describe import public_methods

    methods = public_methods(_PydanticWithFieldAndValidator)
    method_names = {m.name for m in methods}
    assert "validate_name" not in method_names


def test_describe_object_populates_fields_for_pydantic_model() -> None:
    descriptor = describe_object(
        surface="test.surface",
        symbol="_PydanticWithFieldAndValidator",
        obj=_PydanticWithFieldAndValidator,
        summary="test model",
        constraints=(),
        examples=(),
        see_also=(),
    )
    assert descriptor.kind == "class"
    assert len(descriptor.fields) == 3
    assert "validate_name" not in {m.name for m in descriptor.methods}


def test_describe_object_renders_dataclass_field_descriptions() -> None:
    from dataclasses import dataclass, field

    @dataclass(frozen=True)
    class Sample:
        name: str = field(metadata={"description": "the name"})
        flag: bool = field(default=False, metadata={"description": "is enabled"})

    descriptor = describe_object(
        surface="test.surface",
        symbol="Sample",
        obj=Sample,
        summary="sample dataclass",
        constraints=(),
        examples=(),
        see_also=(),
    )

    data = render_json(descriptor)
    assert data["kind"] == "class"
    assert data["fields"] == [
        {"name": "name", "annotation": "str", "required": True, "description": "the name"},
        {
            "name": "flag",
            "annotation": "bool",
            "required": False,
            "default": "False",
            "description": "is enabled",
        },
    ]


def test_render_json_includes_fields() -> None:
    descriptor = describe_object(
        surface="test.surface",
        symbol="_PydanticWithFieldAndValidator",
        obj=_PydanticWithFieldAndValidator,
        summary="test model",
        constraints=(),
        examples=(),
        see_also=(),
    )
    data = render_json(descriptor)
    assert "fields" in data
    assert len(data["fields"]) == 3
    field_names = {f["name"] for f in data["fields"]}
    assert field_names == {"name", "count", "tag"}
    name_field = next(f for f in data["fields"] if f["name"] == "name")
    assert name_field["annotation"] == "str"
    assert name_field["required"] is True
    assert name_field["description"] == "item name"
    assert "default" not in name_field
    count_field = next(f for f in data["fields"] if f["name"] == "count")
    assert count_field["required"] is False
    assert count_field["default"] == "0"


def test_render_text_includes_fields() -> None:
    descriptor = describe_object(
        surface="test.surface",
        symbol="_PydanticWithFieldAndValidator",
        obj=_PydanticWithFieldAndValidator,
        summary="test model",
        constraints=(),
        examples=(),
        see_also=(),
    )
    text = render_text(descriptor)
    assert "Fields:" in text
    assert "name [str] required" in text
    assert "count [int] optional default=0" in text
    assert "-- item name" in text


def test_family_fold_carries_label_and_members() -> None:
    from marivo.introspection.schema import Descriptor, FamilyFold

    fold = FamilyFold(label="References", members=("DimensionRef", "MetricRef"))
    assert fold.label == "References"
    assert fold.members == ("DimensionRef", "MetricRef")

    descriptor = Descriptor(
        surface="test.surface",
        kind="surface",
        symbol=None,
        summary="s",
        families=(fold,),
    )
    assert descriptor.families == (fold,)


def test_render_json_includes_families() -> None:
    from marivo.introspection.render import render_json
    from marivo.introspection.schema import Descriptor, FamilyFold

    descriptor = Descriptor(
        surface="test.surface",
        kind="surface",
        symbol=None,
        summary="s",
        families=(FamilyFold(label="References", members=("ARef", "BRef")),),
    )
    data = render_json(descriptor)
    assert data["families"] == [{"label": "References", "members": ["ARef", "BRef"]}]


def test_format_family_block_lists_members() -> None:
    from marivo.introspection.render import format_family_block
    from marivo.introspection.schema import FamilyFold

    block = format_family_block(
        (FamilyFold(label="References", members=("ARef", "BRef")),),
        help_call="ms.help",
    )
    text = "\n".join(block)
    assert "Families (call ms.help('<name>') for any member):" in text
    assert "References (2): ARef, BRef" in text
