"""Datasource connection Help specifications."""

from marivo._authoring.model import AuthoringCapability, authoring_effects
from marivo.datasource._help_specs.builders import (
    _LOCAL,
    _capability,
    _inputs,
    _target,
)

constraints = {
    "declare": (
        "datasource_name_global",
        "datasource_backend_type_required",
        "datasource_field_jsonable",
        "datasource_secret_env_ref",
    ),
    "configured": ("datasource_configured",),
}

SPECS: tuple[AuthoringCapability, ...] = (
    _capability(
        "env",
        "marivo.datasource.credentials.inputs.env",
        "Reference a credential lookup name without capturing its value.",
        output="EnvRef",
        example='md.env("PG_PASSWORD")',
        see_also=(_target("duckdb"),),
    ),
    _capability(
        "s3_secret",
        "marivo.datasource.credentials.inputs.s3_secret",
        "Declare scoped static S3 credentials.",
        output="S3Secret",
        example='md.s3_secret(scope="s3://lake/", region="us-east-1", key_id=md.env("KEY"), secret=md.env("SECRET"))',
        see_also=(_target("duckdb"),),
    ),
    _capability(
        "http_bearer_secret",
        "marivo.datasource.credentials.inputs.http_bearer_secret",
        "Declare scoped HTTP bearer credentials.",
        output="HttpBearerSecret",
        example='md.http_bearer_secret(scope="https://api.example/", token=md.env("TOKEN"))',
        see_also=(_target("duckdb"),),
    ),
    _capability(
        "http_headers_secret",
        "marivo.datasource.credentials.inputs.http_headers_secret",
        "Declare scoped custom HTTP headers.",
        output="HttpHeadersSecret",
        example='md.http_headers_secret(scope="https://api.example/", headers={"x-api-key": md.env("KEY")})',
        see_also=(_target("duckdb"),),
    ),
    _capability(
        "duckdb",
        "marivo.datasource.store.builders.duckdb",
        "Build a DuckDB datasource specification with optional URL-scoped S3 and HTTP secrets.",
        output="DatasourceSpec",
        inputs=_inputs(("mapping_key", "DatasourceName")),
        constraints=(*constraints["declare"], "duckdb_secret_scoped"),
        example=(
            'md.duckdb(name="api", secrets=[md.http_bearer_secret(scope="https://api.example/v1/", '
            'token=md.env("API_TOKEN"))])'
        ),
    ),
    _capability(
        "trino",
        "marivo.datasource.store.builders.trino",
        "Build a Trino datasource specification. Exact median/quantile fails source "
        "compilation because Trino has no qualified native exact algorithm. Explicit approx_median/approx_quantile uses "
        "native approx_percentile. Execution qualifies actual types and never substitutes "
        "exact and approximate algorithms; semantic readiness does not qualify execution.",
        output="DatasourceSpec",
        inputs=_inputs(("mapping_key", "DatasourceName")),
        constraints=constraints["declare"],
        example=(
            'md.trino(name="warehouse", host="trino.example", catalog="hive", '
            'user=md.env("TRINO_USER"), auth=md.env("TRINO_AUTH"))'
        ),
    ),
    _capability(
        "mysql",
        "marivo.datasource.store.builders.mysql",
        "Build a MySQL datasource specification.",
        output="DatasourceSpec",
        inputs=_inputs(("mapping_key", "DatasourceName")),
        constraints=constraints["declare"],
        example='md.mysql(name="warehouse", host="mysql.example", database="sales")',
    ),
    _capability(
        "postgres",
        "marivo.datasource.store.builders.postgres",
        "Build a Postgres datasource specification.",
        output="DatasourceSpec",
        inputs=_inputs(("mapping_key", "DatasourceName")),
        constraints=constraints["declare"],
        example='md.postgres(name="warehouse", host="postgres.example", database="sales")',
    ),
    _capability(
        "clickhouse",
        "marivo.datasource.store.builders.clickhouse",
        "Build a ClickHouse datasource specification.",
        output="DatasourceSpec",
        inputs=_inputs(("mapping_key", "DatasourceName")),
        constraints=constraints["declare"],
        example='md.clickhouse(name="warehouse", host="clickhouse.example")',
    ),
    _capability(
        "register",
        "marivo.datasource.inspection.manage.register",
        "Persist a user datasource specification; default is reserved for built-in in-memory DuckDB.",
        output="DatasourceSummary",
        inputs=_inputs(("subject", "DatasourceSpec")),
        effects=authoring_effects("local_metadata_read", mutations=("project_state",)),
        constraints=(
            "datasource_secret_env_ref",
            "datasource_unique_name",
            "datasource_register_outside_loader",
        ),
        example='md.register(md.duckdb(name="warehouse", path=":memory:"))',
        preconditions=("a validated DatasourceSpec", "execution outside model loading"),
    ),
    _capability(
        "remove",
        "marivo.datasource.inspection.manage.remove",
        "Remove one persisted datasource declaration; the built-in default cannot be removed.",
        output="bool",
        inputs=_inputs(("subject", "DatasourceName")),
        effects=authoring_effects("local_metadata_read", mutations=("project_state",)),
        constraints=("datasource_unique_name",),
        example='md.remove("warehouse")',
    ),
    _capability(
        "load",
        "marivo.datasource.inspection.catalog.load",
        "Load the read-only datasource catalog, including built-in default.",
        output="DatasourceCatalog",
        effects=_LOCAL,
        constraints=("datasource_project_roots",),
        preconditions=(
            "Each declaration load uses fresh namespaces for relative model imports, without modifying sys.path; modules remain until process exit. Model absolute imports are unsupported.",
            "Restart Python after package upgrades or ordinary dependency changes; ordinary imports retain Python module caching.",
            "Execution failures report source_location and exception_type. Inspect error.traceback for the original traceback.",
        ),
        example="md.load()",
    ),
    _capability(
        "credential_scope",
        "marivo.datasource.credentials.credential_scope",
        "Select a host resolver for new operations/runtimes; exit restores selection only. "
        "DatasourceCatalog tests and SourceInspection source reads use the scope active at the call; "
        "a sample cache hit does not resolve credentials. A SemanticCatalog or Session retains its captured resolver.",
        output="CredentialScope",
        inputs=_inputs(("dependency", "CredentialResolver")),
        constraints=("credential_resolution", "credential_runtime_binding"),
        example=(
            "class HostResolver:\n"
            "    def resolve(self, request: md.CredentialRequest) -> md.SecretValue:\n"
            "        raise md.DatasourceCredentialError(\n"
            "            reason='missing', reference=request.reference,\n"
            "            datasource=request.datasource, fields=request.fields)\n"
            "with md.credential_scope(resolver=HostResolver()):\n"
            "    result = md.load().test('warehouse')"
        ),
        see_also=(
            _target("CredentialRequest"),
            _target("CredentialResolver"),
            _target("SecretValue"),
            _target("DatasourceCredentialError"),
            _target("DatasourceCredentialScopeError"),
        ),
        repair_kinds=("configure", "reconnect"),
    ),
    _capability(
        "CredentialResolver.resolve",
        "marivo.datasource.credentials.CredentialResolver.resolve",
        "Resolve one reference using host authorization and host-managed service timeouts; return SecretValue or a typed credential failure.",
        kind="method",
        output="SecretValue",
        inputs=_inputs(("receiver", "CredentialResolver"), ("subject", "CredentialRequest")),
        example="value = host_resolver.resolve(request)",
        constraints=("credential_resolution",),
        public_entrypoint="resolver.resolve",
    ),
    _capability(
        "CredentialRequest.show",
        "marivo.datasource.credentials.CredentialRequest.show",
        "Print bounded request facts without resolving credentials or disclosing secret material.",
        kind="method",
        output="None",
        inputs=_inputs(("receiver", "CredentialRequest")),
        example="request.show()",
        public_entrypoint="request.show",
    ),
    _capability(
        "SecretValue.reveal",
        "marivo.datasource.credentials.SecretValue.reveal",
        "Read secret material explicitly for a trusted driver adapter; never print or persist it.",
        kind="method",
        output="str",
        inputs=_inputs(("receiver", "SecretValue")),
        example="value = md.SecretValue('example-only').reveal()",
        public_entrypoint="secret.reveal",
    ),
)
