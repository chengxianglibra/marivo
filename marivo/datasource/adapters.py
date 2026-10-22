"""Owned, expression-only source reads for governed datasource consumers.

Provider selection is pure. Opening a selected session is the only step that
loads its optional backend driver. The session owns issued reads and its backend lifetime.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal
from uuid import uuid4

import ibis
import ibis.expr.operations as ops
import ibis.expr.types as ir
import pandas as pd
import pyarrow as pa
from ibis.backends import BaseBackend

from marivo import _execution_log
from marivo.datasource import credentials as cr
from marivo.datasource._lifetime import BackendLease
from marivo.datasource.engines import (
    SUPPORTED_BACKEND_TYPES,
    EngineProfile,
    require_profile_for_backend_type,
)
from marivo.datasource.errors import (
    DatasourceSourceCapabilityError,
    repair,
)
from marivo.datasource.ir import (
    CsvSourceIR,
    DatasourceIR,
    JsonSourceIR,
    ParquetSourceIR,
    QueryParamScalar,
    QueryParamScalarList,
    TableSourceIR,
)
from marivo.datasource.table_source import table_source_expression

DURATION_UNIT_METADATA_KEY = b"marivo:duration_unit"
TIMESTAMP_UNIT_METADATA_KEY = b"marivo:timestamp_unit"

SourceIR = TableSourceIR | CsvSourceIR | ParquetSourceIR | JsonSourceIR
Parameter = str | int | float | bool | bytes | Decimal | date | datetime | None
Termination = Literal["local_closed", "remote_unknown", "remote_confirmed"]


def _invalid(expected: str, received: str) -> DatasourceSourceCapabilityError:
    return DatasourceSourceCapabilityError(
        message="Governed source read cannot satisfy its binding.",
        expected=expected,
        received=received,
        location="datasource adapter",
        repair=repair(
            kind="inspect",
            canonical_id="inspect",
            action="Bind the declared source and use an expression qualified for this datasource.",
        ),
    )


def provider_for(backend: str) -> EngineProfile:
    """Select one backend without loading any driver or opening a connection."""
    return require_profile_for_backend_type(backend)


def provider_names() -> tuple[str, ...]:
    return SUPPORTED_BACKEND_TYPES


def _probe_backend(backend_name: str, backend: BaseBackend) -> None:
    expression = ibis.literal(1).name("probe").as_table()
    sql = backend.compile(expression, limit=None)
    query = _execution_log.QueryLog(
        sql,
        backend=backend_name,
        purpose="datasource.connectivity",
        sensitive=bool(cr.injected_values(backend)),
    )
    try:
        result = backend.execute(expression, params={}, limit=None)
        if (
            not isinstance(result, pd.DataFrame)
            or tuple(result.columns) != ("probe",)
            or len(result) != 1
            or result.iloc[0, 0] != 1
        ):
            raise _invalid("one Ibis literal result equal to 1", repr(result))
        query.rows = len(result)
    except BaseException as error:
        query.error = error
        raise
    finally:
        query.finish("failed" if query.error is not None else "succeeded")


@dataclass(frozen=True, slots=True)
class PhysicalFacts:
    """Scoped physical observations; unknown never grants business coverage."""

    source_identity: str
    schema: pa.Schema
    observed_scope: Literal["unknown", "bounded_sample", "complete"] = "unknown"
    business_coverage: Literal["unknown"] = "unknown"


@dataclass(frozen=True, slots=True, eq=False)
class BoundSource:
    source: SourceIR
    relation: ir.Table
    facts: PhysicalFacts
    _owner: object


@dataclass(frozen=True, slots=True)
class PhysicalRequirement:
    """Physical operations required by an Analysis-owned method version."""

    method_id: str
    version: int
    operations: frozenset[
        Literal["scan", "filter", "project", "group", "count", "join", "union", "window", "sort"]
    ]


@dataclass(frozen=True, slots=True, eq=False)
class QualifiedSource:
    binding: BoundSource
    requirement: PhysicalRequirement
    _owner: object


@dataclass(frozen=True, slots=True, eq=False)
class CompiledRead:
    """Opaque session-issued evidence of one exact Ibis compilation."""

    expression: ir.Expr
    sql: str
    params: tuple[tuple[ir.Scalar, Parameter], ...]
    purpose: str
    schema: pa.Schema
    source_identity: str
    _owner: object


@dataclass(frozen=True, slots=True)
class _IssuedRead:
    expression: ir.Expr
    sql: str
    params: tuple[tuple[ir.Scalar, Parameter], ...]
    purpose: str
    schema: pa.Schema
    source_identity: str
    requirements: tuple[PhysicalRequirement, ...]


@dataclass(slots=True)
class SourceSubmission:
    purpose: str
    source_identity: str
    expression_identity: int
    sql: str
    state: Literal["submitted", "succeeded", "failed"] = "submitted"
    connection_disconnected: bool = False
    termination: Termination | None = None


class SourceSession:
    """Own one selected backend, its bound relations and compiled submissions."""

    def __init__(
        self,
        provider: EngineProfile,
        datasource: DatasourceIR,
        backend: BaseBackend,
        *,
        owns_backend: bool = True,
        backend_lease: BackendLease | None = None,
        project_root: Path | None = None,
    ):
        if datasource.backend_type != provider.name:
            raise _invalid(provider.name, datasource.backend_type)
        if backend.name != provider.name:
            raise _invalid(provider.name, backend.name)
        if backend_lease is not None and (not owns_backend or backend_lease.backend is not backend):
            raise _invalid("an owned lease for this exact backend", "borrowed or mismatched lease")
        self._log_context = _execution_log.snapshot(project_root)
        self._log_root = self._log_context.root
        self.provider = provider
        self.datasource = datasource
        self._backend = backend
        self._backend_lease = backend_lease or BackendLease(backend) if owns_backend else None
        self._backend_disconnected = False
        self._token = object()
        self._issued: dict[int, tuple[CompiledRead, _IssuedRead]] = {}
        self._bindings_by_identity: dict[str, tuple[SourceIR, dict[str, object]]] = {}
        self._bound_sources: dict[str, BoundSource] = {}
        self._query_string_facts: dict[tuple[str, str], bool] = {}
        self._query_decoded_strings: dict[str, frozenset[str]] = {}
        self._key_relations: dict[str, ops.InMemoryTable] = {}
        self._key_request_limit: tuple[int, str] | None = None
        self._verified_source_timezones: set[str] = set()
        self._closed = False
        self.submissions: list[SourceSubmission] = []
        self._query_logs: dict[int, _execution_log.QueryLog] = {}
        self.domain_authority: dict[str, object] | None = None

    def __enter__(self) -> SourceSession:
        self._ensure_open()
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        self.close()

    def verify_source_timezones(self, names: tuple[str, ...]) -> None:
        """Verify retained native projection zones on this exact open connection."""
        from marivo.datasource.timezone import verify_source_timezone

        self._ensure_open()
        for name in names:
            if name in self._verified_source_timezones:
                continue
            with cr.backend_errors(self._backend):
                verify_source_timezone(self._backend, name)
            self._verified_source_timezones.add(name)

    def _ensure_open(self) -> None:
        if self._closed:
            raise _invalid("an open source session", "closed source session")

    def _bind_key_relation(self, data: pa.Table) -> ir.Table:
        """Capture one invocation-local Arrow key relation for this DuckDB owner."""
        self._ensure_open()
        if self.provider.name != "duckdb":
            raise _invalid("DuckDB for an invocation-local key relation", self.provider.name)
        if (
            not data.schema.names
            or len(set(data.schema.names)) != len(data.schema.names)
            or any(pa.types.is_null(field.type) for field in data.schema)
        ):
            raise _invalid("unique key columns with concrete Arrow types", str(data.schema))
        relation = ibis.memtable(data, schema=ibis.Schema.from_pyarrow(data.schema))
        node = relation.op().copy(name="_marivo_keys_" + uuid4().hex)
        self._key_relations[node.name] = node
        return node.to_expr()

    def _owns_key_relation(self, node: ops.Relation) -> bool:
        if not isinstance(node, ops.InMemoryTable):
            return False
        owned = self._key_relations.get(node.name)
        # Ibis rewrites may rebuild the node while retaining its immutable carrier.
        return owned is not None and node.schema == owned.schema and node.data is owned.data

    def _mysql_key_capacity(self) -> int:
        """Qualify invocation-local key payloads against the active MySQL session."""
        self._ensure_open()
        if self.provider.name != "mysql":
            raise _invalid("MySQL key request capacity", self.provider.name)
        if self._key_request_limit is None:
            from marivo.datasource.engines.mysql import query_packet_limit

            self._key_request_limit = query_packet_limit(self._backend)
        return self._key_request_limit[0]

    def bind(
        self,
        source: SourceIR,
        *,
        source_identity: str,
        source_params: Mapping[str, QueryParamScalar | QueryParamScalarList] | None = None,
    ) -> BoundSource:
        """Bind through the provider while sanitizing injected credential failures."""
        with cr.backend_errors(self._backend):
            return self._bind_source(
                source, source_identity=source_identity, source_params=source_params
            )

    def _bind_source(
        self,
        source: SourceIR,
        *,
        source_identity: str,
        source_params: Mapping[str, QueryParamScalar | QueryParamScalarList] | None = None,
    ) -> BoundSource:
        """Resolve a typed physical source without accepting executable SQL."""
        self._ensure_open()
        if not source_identity:
            raise _invalid("a nonempty exact source identity", "empty identity")
        supplied_source_params: dict[str, object] = dict(source_params or {})
        prior = self._bindings_by_identity.get(source_identity)
        if prior is not None and prior != (source, supplied_source_params):
            raise _invalid("one source and parameter binding per identity", "identity reused")
        if prior is not None:
            return self._bound_sources[source_identity]
        if isinstance(source, TableSourceIR):
            if source_params:
                raise _invalid(
                    "no runtime parameters for a table source", repr(tuple(source_params))
                )
            relation = table_source_expression(self._backend, source)
        elif isinstance(source, CsvSourceIR) and self.provider.name == "duckdb":
            if source_params:
                raise _invalid("no runtime parameters for a CSV source", repr(tuple(source_params)))
            csv_options: dict[str, object] = {}
            if not source.header:
                csv_options["header"] = False
            if source.delimiter != ",":
                csv_options["delim"] = source.delimiter
            relation = self._backend.read_csv(source.path, **csv_options)
            if source.columns:
                relation = relation.select(
                    *(relation[physical].name(output) for output, physical in source.columns)
                )
        elif isinstance(source, ParquetSourceIR) and self.provider.name == "duckdb":
            if source_params:
                raise _invalid(
                    "no runtime parameters for a Parquet source", repr(tuple(source_params))
                )
            relation = self._backend.read_parquet(
                source.path, hive_partitioning=source.hive_partitioning
            )
            if source.columns is not None:
                relation = relation.select(*source.columns)
        elif isinstance(source, JsonSourceIR) and self.provider.name == "duckdb":
            from marivo.datasource.json_source import read_json_source

            relation = read_json_source(self._backend, source, source_params=source_params)
        else:
            raise _invalid("a source qualified for the selected backend", type(source).__name__)
        if not isinstance(relation, ir.Table):
            raise _invalid("an Ibis table relation", type(relation).__name__)
        physical_schema = relation.schema().to_pyarrow()
        if isinstance(source, ParquetSourceIR):
            from pathlib import Path

            import pyarrow.parquet as pq

            # Arrow's fixed Duration logical metadata survives Parquet even though
            # DuckDB presents its lossless tick carrier as BIGINT.
            if Path(source.path).is_file():
                arrow_schema = pq.read_schema(source.path)
                fields = []
                for field in physical_schema:
                    index = arrow_schema.get_field_index(field.name)
                    original = arrow_schema.field(index).type if index >= 0 else None
                    if original is not None and pa.types.is_duration(original):
                        if field.type != pa.int64():
                            raise _invalid("int64 Parquet duration ticks", str(field.type))
                        field = field.with_metadata(
                            {
                                **(field.metadata or {}),
                                DURATION_UNIT_METADATA_KEY: original.unit.encode("ascii"),
                            }
                        )
                    if original is not None and pa.types.is_timestamp(original):
                        if not pa.types.is_timestamp(field.type):
                            raise _invalid("timestamp Parquet carrier", str(field.type))
                        field = field.with_metadata(
                            {
                                **(field.metadata or {}),
                                TIMESTAMP_UNIT_METADATA_KEY: original.unit.encode("ascii"),
                            }
                        )
                    fields.append(field)
                physical_schema = pa.schema(fields, metadata=physical_schema.metadata)
        binding = BoundSource(
            source,
            relation,
            PhysicalFacts(source_identity, physical_schema),
            self._token,
        )
        self._bindings_by_identity[source_identity] = (source, supplied_source_params)
        self._bound_sources[source_identity] = binding
        return binding

    def binding_for(self, source_identity: str) -> BoundSource:
        """Return this session's exact typed binding for a governed consumer."""
        self._ensure_open()
        binding = self._bound_sources.get(source_identity)
        if binding is None:
            raise _invalid("a source bound by this session", source_identity)
        return binding

    def qualify(self, binding: BoundSource, need: PhysicalRequirement) -> QualifiedSource:
        """Make the single physical qualification decision for basic reads."""
        self._ensure_open()
        if binding._owner is not self._token:
            raise _invalid("a source bound by this session", "foreign source binding")
        if not isinstance(self._backend, BaseBackend):
            raise _invalid(
                "a verified basic read route for the selected backend",
                f"{self.provider.name} backend is not a live Ibis backend",
            )
        supported = frozenset({"scan", "filter", "project", "group", "count"})
        if self.provider.name == "duckdb":
            supported |= {"join", "union", "window", "sort"}
        elif self.provider.name in ("postgres", "mysql", "trino", "clickhouse"):
            supported |= {"join", "union"}
            if need.method_id == "query.source_read":
                supported |= {"window", "sort"}
        if (
            not need.method_id
            or need.version < 1
            or not need.operations
            or not need.operations <= supported
        ):
            raise _invalid("one registered basic physical requirement", repr(need))
        return QualifiedSource(binding, need, self._token)

    def clickhouse_table_engine(self, binding: BoundSource) -> str | None:
        """Inspect the physical engine of this session's named ClickHouse source."""
        self._ensure_open()
        if binding._owner is not self._token:
            raise _invalid("a source bound by this session", "foreign source binding")
        if self.provider.name != "clickhouse" or not isinstance(binding.source, TableSourceIR):
            raise _invalid("a named ClickHouse table or view", repr(binding.source))
        from marivo.datasource.engines.clickhouse import table_engine

        with cr.backend_errors(self._backend):
            return table_engine(self._backend, self.datasource, binding.source)

    def compile(
        self,
        qualified: QualifiedSource | Sequence[QualifiedSource],
        expression: ir.Expr,
        *,
        params: Mapping[ir.Scalar, Parameter] | None = None,
        purpose: str,
        expected_schema: pa.Schema,
    ) -> CompiledRead:
        """Issue a handle for one bound expression and unmodified Ibis SQL."""
        with cr.backend_errors(self._backend):
            return self._compile_read(
                qualified,
                expression,
                params=params,
                purpose=purpose,
                expected_schema=expected_schema,
            )

    def _compile_read(
        self,
        qualified: QualifiedSource | Sequence[QualifiedSource],
        expression: ir.Expr,
        *,
        params: Mapping[ir.Scalar, Parameter] | None = None,
        purpose: str,
        expected_schema: pa.Schema,
    ) -> CompiledRead:
        """Issue a handle for one bound expression and unmodified Ibis SQL."""
        self._ensure_open()
        sources = (qualified,) if isinstance(qualified, QualifiedSource) else tuple(qualified)
        if not sources or any(
            item._owner is not self._token or item.binding._owner is not self._token
            for item in sources
        ):
            raise _invalid("this session's qualified sources", "empty or foreign qualification")
        if not purpose:
            raise _invalid("a nonempty read purpose", "empty purpose")
        if not isinstance(expression, ir.Expr):
            raise _invalid("an Ibis expression", type(expression).__name__)
        relation_nodes = tuple(item.binding.relation.op() for item in sources)
        expression_relations = tuple(expression.op().find(ops.Relation))
        selected_ids = {item.binding.facts.source_identity for item in sources}
        present_ids = {
            item.binding.facts.source_identity
            for item in sources
            if any(node == item.binding.relation.op() for node in expression_relations)
        }
        if present_ids != selected_ids:
            raise _invalid("an expression derived from the bound relation", "unbound expression")
        physical_leaves = (
            ops.DatabaseTable,
            ops.UnboundTable,
            ops.InMemoryTable,
            ops.SQLQueryResult,
        )
        allowed_leaves = {
            node for relation_node in relation_nodes for node in relation_node.find(physical_leaves)
        }
        observed_leaves = set(expression.op().find(physical_leaves))
        if any(
            node not in allowed_leaves and not self._owns_key_relation(node)
            for node in observed_leaves
        ):
            raise _invalid(
                "only physical inputs from the bound source", "additional physical input"
            )
        actual_schema = expression.as_table().schema().to_pyarrow()
        if not actual_schema.equals(expected_schema, check_metadata=False):
            raise _invalid(str(actual_schema), str(expected_schema))
        supplied = dict(params or {})
        sql = self._backend.compile(expression.as_table(), params=supplied, limit=None)
        if not isinstance(sql, str) or not sql:
            raise _invalid("nonempty Ibis compiled SQL", type(sql).__name__)
        if self.provider.name == "clickhouse":
            from marivo.datasource.engines.clickhouse import validate_query_size

            validate_query_size(self._backend, sql)
        if self._key_request_limit is not None:
            limit, encoding = self._key_request_limit
            # MySQLdb encodes the SQL with connection.encoding. COM_QUERY adds
            # one command byte; key Arrow size is not the request size.
            size = len(sql.encode(encoding)) + 1
            if size > limit:
                raise DatasourceSourceCapabilityError(
                    message="The MySQL complete-key request exceeds its native capacity.",
                    expected=f"an encoded MySQL key request within max_allowed_packet={limit} bytes",
                    received=f"{size} request bytes",
                    repair=repair(
                        kind="reconnect",
                        canonical_id="mysql",
                        action="Reduce the selected key domain or increase the datasource packet limit and reconnect.",
                    ),
                )
        source_identity = "|".join(sorted({item.binding.facts.source_identity for item in sources}))
        issued = CompiledRead(
            expression,
            sql,
            tuple(supplied.items()),
            purpose,
            expected_schema,
            source_identity,
            self._token,
        )
        self._issued[id(issued)] = (
            issued,
            _IssuedRead(
                expression,
                sql,
                issued.params,
                purpose,
                expected_schema,
                source_identity,
                tuple(item.requirement for item in sources),
            ),
        )
        return issued

    def _issued_read(self, read: CompiledRead) -> _IssuedRead:
        self._ensure_open()
        stored = self._issued.get(id(read))
        if read._owner is not self._token or stored is None or stored[0] is not read:
            raise _invalid("a compiled read issued by this session", "foreign or fabricated read")
        proof = stored[1]
        if (
            read.sql != proof.sql
            or read.expression is not proof.expression
            or read.params is not proof.params
            or read.purpose != proof.purpose
            or read.source_identity != proof.source_identity
            or not read.schema.equals(proof.schema, check_metadata=False)
        ):
            raise _invalid("the unchanged compiled read", "altered compiled read")
        return proof

    def execute(self, read: CompiledRead) -> pd.DataFrame:
        """Execute one session-issued Ibis read and return its pandas result."""
        proof = self._issued_read(read)
        submission = SourceSubmission(
            proof.purpose, proof.source_identity, id(proof.expression.op()), proof.sql
        )
        self.submissions.append(submission)
        query = _execution_log.QueryLog(
            proof.sql,
            backend=self.provider.name,
            purpose=proof.purpose,
            sensitive=bool(cr.injected_values(self._backend)),
            project_root=self._log_root,
            datasource=self.datasource.name,
            source_identity=proof.source_identity,
        )
        self._query_logs[id(submission)] = query
        try:
            with cr.backend_errors(self._backend):
                table = proof.expression.as_table()
                if self.provider.name in {"duckdb", "trino"} or (
                    self.provider.name == "postgres"
                    and all(
                        requirement.method_id == "query.source_read" and requirement.version == 1
                        for requirement in proof.requirements
                    )
                ):
                    # Preserve native integer/Decimal values and nulls before pandas rounding.
                    arrow = self._backend.to_pyarrow(table, params=dict(proof.params), limit=None)
                    result = arrow.to_pandas(
                        types_mapper=lambda dtype: (
                            pd.ArrowDtype(dtype) if pa.types.is_integer(dtype) else None
                        )
                    )
                else:
                    result = self._backend.execute(table, params=dict(proof.params), limit=None)
            if not isinstance(result, pd.DataFrame):
                raise _invalid("an Ibis pandas DataFrame result", type(result).__name__)
            if tuple(result.columns) != tuple(proof.schema.names):
                raise _invalid(
                    f"DataFrame columns in compiled order {proof.schema.names!r}",
                    repr(tuple(result.columns)),
                )
            query.rows = len(result)
            submission.state = "succeeded"
            return result
        except BaseException as error:
            submission.state = "failed"
            query.error = error
            if isinstance(error, Exception) and cr.injected_values(self._backend):
                raise cr.safe_backend_exception(error, self._backend) from None
            raise
        finally:
            query.finish("failed" if query.error is not None else "succeeded")

    def collect_bounded(
        self,
        expression: ir.Table,
        *,
        source_identities: Sequence[str] | None = None,
        purpose: str,
        max_rows: int,
    ) -> pd.DataFrame:
        """Execute a row-bounded authoring read through the session's exact bindings."""
        if type(max_rows) is not int or max_rows < 1:
            raise _invalid("a positive authoring row bound", repr(max_rows))
        if source_identities is None:
            relations = tuple(expression.op().find(ops.Relation))
            source_identities = tuple(
                identity
                for identity, binding in self._bound_sources.items()
                if any(binding.relation.op() == relation for relation in relations)
            )
        sources = tuple(
            self.qualify(
                self.binding_for(identity),
                PhysicalRequirement(
                    "source_adapter.basic",
                    1,
                    frozenset({"scan", "filter", "project", "group", "count"}),
                ),
            )
            for identity in source_identities
        )
        bounded = expression.limit(max_rows)
        read = self.compile(
            sources,
            bounded,
            purpose=purpose,
            expected_schema=bounded.schema().to_pyarrow(),
        )
        return self.execute(read)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        failure: BaseException | None = None
        self._issued.clear()
        self._bindings_by_identity.clear()
        self._bound_sources.clear()
        self._verified_source_timezones.clear()
        for node in self._key_relations.values():
            try:
                self._backend.drop_view(node.name, force=True)
            except BaseException as error:
                if failure is None:
                    failure = error
        self._key_relations.clear()
        try:
            self._release_backend()
        except BaseException as error:
            if failure is None:
                failure = error
        if failure is not None:
            raise failure

    def _release_backend(self) -> None:
        if self._backend_lease is not None and not self._backend_disconnected:
            if not self._backend_lease.close(suppress_errors=False):
                raise _invalid(
                    "confirmed owned connection release", "connection release unconfirmed"
                )
            self.mark_backend_disconnected()

    def mark_backend_disconnected(self) -> None:
        """Record only a connection release confirmed by the backend owner."""
        self._backend_disconnected = True
        for submission in self.submissions:
            submission.connection_disconnected = True
            if submission.state == "failed":
                submission.termination = (
                    "local_closed" if self.provider.name == "duckdb" else "remote_unknown"
                )
                query = self._query_logs.get(id(submission))
                if query is not None:
                    _execution_log.emit(
                        "query.cleanup",
                        context=query.context,
                        termination=submission.termination,
                        connection_disconnected=True,
                    )
