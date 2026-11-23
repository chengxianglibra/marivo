"""Column metadata retained by one governed source session."""

from __future__ import annotations

from collections.abc import Callable

import ibis.expr.operations as ops
from ibis.backends import BaseBackend

from marivo.datasource.engines.base import EngineProfile
from marivo.datasource.ir import (
    CsvSourceIR,
    DatasourceIR,
    JsonSourceIR,
    ParquetSourceIR,
    TableSourceIR,
)
from marivo.datasource.runtime.read_types import BoundSource


class SourceStringFacts:
    def __init__(
        self, profile: EngineProfile, datasource: DatasourceIR, backend: BaseBackend
    ) -> None:
        self._profile = profile
        self._datasource = datasource
        self._backend = backend
        self._query_string_facts: dict[tuple[str, str], bool] = {}
        self._query_decoded_strings: dict[str, frozenset[str]] = {}
        self._original_string_sets: dict[str, frozenset[str]] = {}

    def original_input_qualified(
        self,
        bound: BoundSource,
        leaf: ops.Node,
        table_join_issue: Callable[[BoundSource], str | None],
    ) -> bool:
        """Qualify one physical input for native original recomputation."""
        inspect = self._profile.dialect.original_input
        if inspect is not None:
            return inspect(
                self._backend,
                leaf.to_expr(),
                file_source=isinstance(bound.source, (CsvSourceIR, JsonSourceIR, ParquetSourceIR)),
            )
        if not isinstance(bound.source, TableSourceIR) or not isinstance(leaf, ops.DatabaseTable):
            return False
        if self._profile.table_join_issue is not None:
            return table_join_issue(bound) is None
        return self._profile.query_policy.key_fusion != "none"

    def original_string_issue(self, bound: BoundSource, column: str) -> str | None:
        """Qualify an original source string without reading business rows."""
        policy = self._profile.dialect.original_strings
        if policy is None:
            return None
        if not isinstance(bound.source, TableSourceIR):
            return "Remote original strings require a physical table collation"
        try:
            if policy.columns is not None:
                identity = bound.facts.source_identity
                if identity not in self._original_string_sets:
                    self._original_string_sets[identity] = policy.columns(
                        self._backend, self._datasource, bound.source
                    )
                supported = column in self._original_string_sets[identity]
            else:
                assert policy.column is not None
                supported = policy.column(self._backend, self._datasource, bound.source, column)
        except Exception:
            return policy.unavailable
        return None if supported else policy.unsupported.format(column=column)

    def query_string_columns(self, bound: BoundSource) -> frozenset[str]:
        """Read and retain decoded string columns for this invocation."""
        inspect = self._profile.dialect.query_string_columns
        assert inspect is not None and isinstance(bound.source, TableSourceIR)
        identity = bound.facts.source_identity
        if identity not in self._query_decoded_strings:
            self._query_decoded_strings[identity] = inspect(
                self._backend, self._datasource, bound.source
            )
        return self._query_decoded_strings[identity]

    def original_key_string(self, bound: BoundSource, column: str) -> bool:
        """Admit a source string whose native equality matches returned bytes."""
        inspect = self._profile.dialect.original_string_column
        assert inspect is not None
        fact = (bound.facts.source_identity, column)
        if fact not in self._query_string_facts:
            self._query_string_facts[fact] = isinstance(bound.source, TableSourceIR) and inspect(
                self._backend, self._datasource, bound.source, column
            )
        return self._query_string_facts[fact]
