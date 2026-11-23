"""Owned, expression-only source reads for governed datasource consumers.

Provider selection is pure. Opening a selected session is the only step that
loads its optional backend driver. The session owns issued reads and its backend lifetime.
"""

from __future__ import annotations

import random
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import ibis
import ibis.expr.operations as ops
import ibis.expr.schema as sch
import ibis.expr.types as ir
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from ibis.backends import BaseBackend

from marivo._kernel import execution_log as _execution_log
from marivo._kernel.validation import nonempty_text
from marivo.datasource import credentials as cr
from marivo.datasource._expression_fields import physical_fields
from marivo.datasource._lifetime import BackendLease
from marivo.datasource.arrow_types import native_pandas_type
from marivo.datasource.engines import (
    SUPPORTED_BACKEND_TYPES,
    EngineProfile,
    require_profile_for_backend_type,
)
from marivo.datasource.engines._aggregate_compilers import (
    DuckDBCompilationMemo,
    compile_distributed_query,
    configure_aggregate_compiler,
)
from marivo.datasource.engines.calendar_relations import CalendarData, memory_calendar
from marivo.datasource.engines.query import KeyRows
from marivo.datasource.errors import (
    DatasourceSourceCapabilityError,
    _execution_error_fields,
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
from marivo.datasource.runtime.column_storage import StoredProjectionFacts
from marivo.datasource.runtime.json_source import JsonResponseCache, read_json_source
from marivo.datasource.runtime.read_types import (
    DURATION_UNIT_METADATA_KEY,
    TIMESTAMP_UNIT_METADATA_KEY,
    BoundSource,
    CompiledRead,
    Parameter,
    PhysicalFacts,
    PhysicalRequirement,
    QualifiedSource,
    SourceIR,
    SourceSubmission,
    _IssuedRead,
    issued_by,
)
from marivo.datasource.runtime.string_facts import SourceStringFacts
from marivo.datasource.runtime.table_source import table_source_expression
from marivo.datasource.runtime.timezone import verify_source_timezone

# Upper backoff bounds before each resubmission; each wait is jittered to [50%, 100%].
_SOURCE_RETRY_DELAYS_SECONDS = (1.0, 4.0)


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


def _require_decimal_transport(frame: pd.DataFrame, schema: pa.Schema) -> None:
    """Reject floating transport only when the actual issued SQL returns Decimal."""
    for field in schema:
        if pa.types.is_decimal(field.type) and pa.types.is_floating(
            pa.array(frame[field.name], from_pandas=True).type
        ):
            raise DatasourceSourceCapabilityError(
                message="Exact Decimal values arrived through floating transport.",
                expected=f"{field.name}: exact {field.type} values",
                received=f"{field.name}: floating carrier",
                location="datasource adapter",
                repair=repair(
                    kind="inspect",
                    canonical_id="inspect",
                    action="Use a reader or source view preserving Decimal values. Do not cast rounded floats back to Decimal; original integer precision cannot be recovered.",
                ),
            )


def provider_for(backend: str) -> EngineProfile:
    """Select one backend without loading any driver or opening a connection."""
    return require_profile_for_backend_type(backend)


def provider_names() -> tuple[str, ...]:
    return SUPPORTED_BACKEND_TYPES


def _parquet_physical_schema(source: ParquetSourceIR, physical_schema: pa.Schema) -> pa.Schema:
    # Arrow's fixed Duration logical metadata survives Parquet even though
    # DuckDB presents its lossless tick carrier as BIGINT.
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
    return pa.schema(fields, metadata=physical_schema.metadata)


def expression_backend_name(expression: ir.Expr) -> str:
    """Identify the backend actually bound to an Ibis expression."""
    name: str = expression._find_backend().name  # noqa: SLF001 - Ibis's public lookup may choose a default backend.
    return name


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
        json_responses: JsonResponseCache | None = None,
    ):
        if datasource.backend_type != provider.name:
            raise _invalid(provider.name, datasource.backend_type)
        if backend.name != provider.name:
            raise _invalid(provider.name, backend.name)
        if backend_lease is not None and (not owns_backend or backend_lease.backend is not backend):
            raise _invalid("an owned lease for this exact backend", "borrowed or mismatched lease")
        configure_aggregate_compiler(backend)
        self._log_context = _execution_log.snapshot(project_root)
        self._log_root = self._log_context.root
        self.provider = provider
        self.datasource = datasource
        self._backend = backend
        self._backend_lease = backend_lease or BackendLease(backend) if owns_backend else None
        self._backend_disconnected = False
        self._token = object()
        self._issued: dict[int, tuple[CompiledRead, _IssuedRead]] = {}
        self._compilation_memo = DuckDBCompilationMemo(getattr(backend, "compiler", None))
        self._bindings_by_identity: dict[str, tuple[SourceIR, dict[str, object]]] = {}
        self._json_responses = json_responses
        self._bound_sources: dict[str, BoundSource] = {}
        self.stored_projection = StoredProjectionFacts(provider, datasource, backend, self._token)
        self.string_facts = SourceStringFacts(provider, datasource, backend)
        self._table_engines: dict[str, str | None] = {}
        self._table_join_issues: dict[str, str | None] = {}
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

    def bind_key_relation(self, data: pa.Table) -> ir.Table:
        """Capture one invocation-local Arrow key relation for this DuckDB owner."""
        self._ensure_open()
        if not self.provider.query_policy.owns_arrow_key_relations:
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

    def _complete_key_capacity(self) -> int:
        """Qualify invocation-local key payloads against the active MySQL session."""
        self._ensure_open()
        packet_limit = self.provider.query_policy.query_packet_limit
        if packet_limit is None:
            raise _invalid("MySQL key request capacity", self.provider.name)
        if self._key_request_limit is None:
            self._key_request_limit = packet_limit(self._backend)
        return self._key_request_limit[0]

    def complete_key_membership(
        self, schema: sch.Schema, records: KeyRows, values: Mapping[str, ir.Value]
    ) -> ir.BooleanValue | None:
        """Bind exact tuples only where source equality preserves each string key."""
        bind = self.provider.query_policy.bind_key_membership
        if bind is None:
            return None
        for value in values.values():
            if not value.type().is_string():
                continue
            fields = physical_fields(value)
            if not fields:
                return None
            for field in fields:
                bound = next(
                    (
                        item
                        for item in self._bound_sources.values()
                        if field.rel == item.relation.op()
                    ),
                    None,
                )
                if bound is None or not self.string_facts.original_key_string(bound, field.name):
                    return None
        return bind(schema, records, values, self._complete_key_capacity)

    def verify_query_timezones(self, sql: str) -> None:
        """Check each required zone of the issued SQL on this connection."""
        source_timezones = self.provider.dialect.source_timezones
        if source_timezones is not None:
            self.verify_source_timezones(source_timezones(sql))

    def bind(
        self,
        source: SourceIR,
        *,
        source_identity: str,
        source_params: Mapping[str, QueryParamScalar | QueryParamScalarList] | None = None,
    ) -> BoundSource:
        """Bind through the provider while sanitizing injected credential failures."""
        with cr.backend_errors(self._backend):
            return self.bind_source(
                source, source_identity=source_identity, source_params=source_params
            )

    def bind_source(
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
            relation = self._read_csv(source)
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
            relation = read_json_source(
                self._backend, source, source_params=source_params, responses=self._json_responses
            )
        else:
            raise _invalid("a source qualified for the selected backend", type(source).__name__)
        if not isinstance(relation, ir.Table):
            raise _invalid("an Ibis table relation", type(relation).__name__)
        physical_schema = relation.schema().to_pyarrow()
        if isinstance(source, ParquetSourceIR) and Path(source.path).is_file():
            physical_schema = _parquet_physical_schema(source, physical_schema)
        binding = BoundSource(
            source,
            relation,
            PhysicalFacts(source_identity, physical_schema),
            self._token,
        )
        self._bindings_by_identity[source_identity] = (source, supplied_source_params)
        self._bound_sources[source_identity] = binding
        return binding

    def _read_csv(self, source: CsvSourceIR) -> ir.Table:
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
        return relation

    def uses_native_provider(self) -> bool:
        backend_name: str = self._backend.name
        return backend_name == self.provider.name

    def bindings(self) -> Iterable[BoundSource]:
        return self._bound_sources.values()

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
        if not issued_by(binding, self._token):
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

    def table_engine(self, binding: BoundSource) -> str | None:
        """Inspect the physical engine of this session's named ClickHouse source."""
        self._ensure_open()
        if not issued_by(binding, self._token):
            raise _invalid("a source bound by this session", "foreign source binding")
        table_engine = self.provider.table_engine
        if table_engine is None or not isinstance(binding.source, TableSourceIR):
            raise _invalid("a named ClickHouse table or view", repr(binding.source))

        identity = binding.facts.source_identity
        if identity not in self._table_engines:
            with cr.backend_errors(self._backend):
                self._table_engines[identity] = table_engine(
                    self._backend, self.datasource, binding.source
                )
        return self._table_engines[identity]

    def table_join_issue(self, binding: BoundSource) -> str | None:
        """Reuse invocation-owned physical topology facts for this exact binding."""
        self._ensure_open()
        if not issued_by(binding, self._token):
            raise _invalid("a source bound by this session", "foreign source binding")
        table_join_issue = self.provider.table_join_issue
        if table_join_issue is None or not isinstance(binding.source, TableSourceIR):
            raise _invalid("a named ClickHouse table or view", repr(binding.source))

        identity = binding.facts.source_identity
        if identity not in self._table_join_issues:
            with cr.backend_errors(self._backend):
                self._table_join_issues[identity] = table_join_issue(
                    self._backend, self.datasource, binding.source
                )
        return self._table_join_issues[identity]

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
        with cr.backend_errors(self._backend), self._compilation_memo.activate():
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
        if any(
            not issued_by(item, self._token) or not issued_by(item.binding, self._token)
            for item in sources
        ):
            raise _invalid("this session's qualified sources", "empty or foreign qualification")
        if not purpose:
            raise _invalid("a nonempty read purpose", "empty purpose")
        if not isinstance(expression, ir.Expr):
            raise _invalid("an Ibis expression", type(expression).__name__)
        if self.provider.name == "duckdb":
            replacements = {}
            for directory in expression.op().find(CalendarData):
                relation = memory_calendar(directory).op()
                (physical,) = relation.find(ops.InMemoryTable)
                owned = self._key_relations.setdefault(directory.name, physical)
                replacements[directory] = relation.replace({physical: owned})
            if replacements:
                expression = expression.op().replace(replacements).to_expr()
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
        if (
            self.provider.name == "clickhouse"
            and all(
                item.requirement.method_id == "query.source_read" and item.requirement.version == 1
                for item in sources
            )
            and expression.op().find((ops.JoinChain, ops.InSubquery))
            and any(self.table_engine(item.binding) == "Distributed" for item in sources)
        ):
            sql = compile_distributed_query(expression.as_table(), supplied)
        else:
            sql = self._backend.compile(expression.as_table(), params=supplied, limit=None)
        if not nonempty_text(sql):
            raise _invalid("nonempty Ibis compiled SQL", type(sql).__name__)
        if self.provider.validate_query is not None:
            self.provider.validate_query(self._backend, sql)
        if self._key_request_limit is not None:
            limit, encoding = self._key_request_limit
            # PyMySQL encodes the SQL with connection.encoding. COM_QUERY adds
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

    def reissue(self, read: CompiledRead, *, purpose: str) -> CompiledRead:
        """Issue an already compiled read under another purpose without recompiling it."""
        proof = self._issued_read(read)
        if not purpose:
            raise _invalid("a nonempty read purpose", "empty purpose")
        issued = CompiledRead(
            proof.expression,
            proof.sql,
            proof.params,
            purpose,
            proof.schema,
            proof.source_identity,
            self._token,
        )
        self._issued[id(issued)] = (issued, replace(proof, purpose=purpose))
        return issued

    def _issued_read(self, read: CompiledRead) -> _IssuedRead:
        self._ensure_open()
        stored = self._issued.get(id(read))
        if not issued_by(read, self._token) or stored is None or stored[0] is not read:
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
            format_error=_execution_error_fields,
            project_root=self._log_root,
            datasource=self.datasource.name,
            source_identity=proof.source_identity,
        )
        self._query_logs[id(submission)] = query
        result: object = None

        def mark_submitted() -> None:
            submission.query_executed = True

        try:
            with cr.backend_errors(self._backend), self._compilation_memo.activate():
                for attempt, delay in enumerate((*_SOURCE_RETRY_DELAYS_SECONDS, None), start=1):
                    try:
                        table = proof.expression.as_table()
                        governed_query = all(
                            need.method_id == "query.source_read" and need.version == 1
                            for need in proof.requirements
                        )
                        use_native = governed_query or not self.provider.issued_query_only
                        read_issued = self.provider.read_issued_query
                        if use_native and read_issued is not None:
                            result = read_issued(
                                self._backend, proof.sql, table, proof.schema, mark_submitted
                            )
                        elif use_native and self.provider.arrow_reads:
                            # Preserve native numbers and floating validity before pandas conversion.
                            submission.query_executed = True
                            arrow = self._backend.to_pyarrow(
                                table, params=dict(proof.params), limit=None
                            )
                            result = arrow.to_pandas(types_mapper=native_pandas_type)
                        else:
                            submission.query_executed = True
                            result = self._backend.execute(
                                table, params=dict(proof.params), limit=None
                            )
                        break
                    except Exception as error:
                        # Classify the raw driver failure before credential sanitization.
                        if delay is None or not self.provider.transient_failure(error):
                            raise
                        waited = delay * random.uniform(0.5, 1.0)
                        _execution_log.emit(
                            "query.retry",
                            context=query.context,
                            severity="WARNING",
                            fields=query.failure_fields(error),
                            attempt=attempt,
                            delay_seconds=round(waited, 3),
                        )
                        time.sleep(waited)
            if not isinstance(result, pd.DataFrame):
                raise _invalid("an Ibis pandas DataFrame result", type(result).__name__)
            if tuple(result.columns) != tuple(proof.schema.names):
                raise _invalid(
                    f"DataFrame columns in compiled order {proof.schema.names!r}",
                    repr(tuple(result.columns)),
                )
            query.rows = len(result)
            _require_decimal_transport(result, proof.schema)
            submission.state = "succeeded"
            return result
        except BaseException as error:
            submission.state = "failed"
            query.error = error
            if (
                isinstance(error, Exception)
                and not isinstance(error, DatasourceSourceCapabilityError)
                and cr.injected_values(self._backend)
            ):
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
        self._compilation_memo.entries.clear()
        self._bindings_by_identity.clear()
        self._bound_sources.clear()
        self._verified_source_timezones.clear()
        for node in self._key_relations.values() if self.provider.name == "duckdb" else ():
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
