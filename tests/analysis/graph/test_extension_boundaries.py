"""Controlled provider boundaries, without granting backend qualification."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import NoReturn

import ibis
import pyarrow as pa
import pytest

import marivo.semantic as ms
from marivo.analysis.compiler.graph_plan import LocalMethodStage
from marivo.analysis.core.graph import Edge, SourceDefinition, SourceLeaf, method_node
from marivo.analysis.core.model import (
    Binding,
    Coordinate,
    Defined,
    DomainSignature,
    Null,
    ObservedQuantity,
    Signature,
    Unknown,
)
from marivo.analysis.core.rules import RowState
from marivo.analysis.methods import builtin
from marivo.analysis.methods.errors import MethodRegistrationError
from marivo.analysis.methods.local import count
from marivo.analysis.methods.physical import (
    Backend,
    Implementation,
    NoTime,
    QualificationKey,
    Qualified,
    ResourceRequirements,
    ScalarType,
    SourceShape,
    Unavailable,
)
from marivo.analysis.methods.registry import REGISTRY, MethodRegistry
from marivo.analysis.methods.semantics import MethodKey
from marivo.datasource.adapters import PhysicalRequirement, SourceSession, provider_for
from marivo.datasource.errors import DatasourceSourceCapabilityError
from marivo.datasource.ir import AiContextIR, DatasourceIR, DatasourceSourceLocation, TableSourceIR


def _input() -> Signature:
    binding = Binding("r96", "sales", "orders", "october")
    key = (Coordinate(ms.ref.entity("sales.customer"), "customer_id", "identity"),)
    domain = DomainSignature(binding, "entity", key, key, "customers")
    return Signature(
        domain,
        ObservedQuantity(
            "revenue",
            ms.ref.metric("sales.revenue"),
            "revenue-graph",
            "CNY",
            "october",
            "order-contribution",
            "strict",
            "sum@v1",
        ),
    )


def _implementation(backend: Backend) -> Implementation:
    return Implementation(
        QualificationKey(
            MethodKey("row.count"),
            (ScalarType("int64"),),
            ("entity",),
            SourceShape(backend, "table", "native", NoTime()),
            "ibis_python",
        ),
        (),
        ("row_state",),
        "checked_int64",
        ResourceRequirements("complete", "producer", None),
        Qualified(
            f"r96.stub.{backend}.count",
            "analysis.methods.local",
            "controlled-provider-boundary-only",
        ),
    )


def _registry(*implementations: Implementation) -> MethodRegistry:
    return MethodRegistry(
        (replace(REGISTRY.lookup(MethodKey("row.count")), implementations=implementations),)
    )


def test_provider_replacement_preserves_business_kernel(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only the physical declaration changes; the real core and count consumer run."""
    source = _input()
    params = RowState(
        "count",
        DomainSignature(source.domain.binding, "singleton", (), (), "all"),
        "count",
        "count_all",
    )
    providers = (_implementation("duckdb"), _implementation("sqlite"))

    def declarations(method: MethodKey) -> tuple[Implementation, ...]:
        assert method == MethodKey("row.count")
        return providers

    monkeypatch.setattr(builtin, "implementations", declarations)
    expected = REGISTRY.derive((source,), params)
    results = []
    for candidate in providers:
        chosen = _registry(candidate).select(candidate.key, (source,), params)
        assert chosen.derivation == expected
        assert isinstance(candidate.key.shape, SourceShape)
        leaf = SourceLeaf(
            SourceDefinition(
                ms.ref.metric("sales.revenue"),
                "revenue-graph",
                ms.ref.datasource("source"),
                candidate.key.shape,
            ),
            source,
            ScalarType("int64"),
        )
        node = method_node((Edge("quantity", leaf),), params, value_type=ScalarType("int64"))
        results.append(
            count(
                LocalMethodStage("count", ("prepared",), node, chosen.implementation),
                (Defined(2), Null("source_null"), Unknown("insufficient_business_coverage")),
            )
        )
    assert results[0] == results[1]
    assert results[0].cell == Defined(3)
    assert results[0].count == 3


def test_missing_or_unavailable_exact_provider_never_borrows_route() -> None:
    source = _input()
    params = RowState(
        "count",
        DomainSignature(source.domain.binding, "singleton", (), (), "all"),
        "count",
        "count_all",
    )
    wanted = _implementation("sqlite")
    other_provider = _implementation("duckdb")
    other_route = replace(wanted, key=replace(wanted.key, route="ibis"))
    for implementations in (
        (other_provider, other_route),
        (
            replace(
                wanted,
                qualification=Unavailable(
                    "blocked", "controlled capability gap", "Qualify this exact provider route."
                ),
            ),
            other_provider,
            other_route,
        ),
    ):
        with pytest.raises(MethodRegistrationError) as caught:
            _registry(*implementations).select(wanted.key, (source,), params)
        assert caught.value.repair is not None
        assert caught.value.expected and caught.value.received and caught.value.repair.action
        assert "qualified" in caught.value.expected


def test_wrong_schema_refuses_before_compilation_or_submission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = ibis.sqlite.connect(tmp_path / "schema.sqlite")
    backend.create_table("facts", schema={"id": "int64", "amount": "int64"})
    datasource = DatasourceIR(
        semantic_id="source",
        name="source",
        backend_type="sqlite",
        fields={},
        env_refs={},
        ai_context=AiContextIR(),
        python_symbol="source",
        location=DatasourceSourceLocation("source.py", 1),
    )
    with SourceSession(provider_for("sqlite"), datasource, backend) as session:
        binding = session.bind(TableSourceIR("facts"), source_identity="facts@v1")
        qualified = session.qualify(
            binding, PhysicalRequirement("r96.schema", 1, frozenset({"scan", "project"}))
        )

        def forbidden_compile(*_args: object, **_kwargs: object) -> NoReturn:
            raise AssertionError("invalid schema reached backend compilation")

        monkeypatch.setattr(backend, "compile", forbidden_compile)
        with pytest.raises(DatasourceSourceCapabilityError) as caught:
            session.compile(
                qualified,
                binding.relation.select("id", "amount"),
                purpose="r96.schema",
                expected_schema=pa.schema([("id", pa.int64()), ("amount", pa.string())]),
            )
        assert caught.value.repair is not None
        assert caught.value.expected and caught.value.received and caught.value.repair.action
        assert not session.submissions
        assert not session._issued and not session._streams
