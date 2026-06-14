"""Datasource errors shared by datasource and analysis APIs."""

from __future__ import annotations

from typing import Any


class DatasourceError(Exception):
    """Base class for datasource errors."""

    def __init__(
        self,
        *,
        message: str,
        hint: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}
        if hint is None:
            from marivo.datasource.constraints import (
                CONSTRAINTS,
                default_constraint_for_error,
            )
            from marivo.introspection.errors import hint_from_catalog

            constraint = default_constraint_for_error(self.kind, self.details)
            if constraint is not None:
                hint = constraint.hint
            else:
                hint = hint_from_catalog(
                    {constraint.id: constraint for constraint in CONSTRAINTS.values()},
                    self.kind,
                )
        self.hint = hint

    @property
    def kind(self) -> str:
        name = type(self).__name__
        return name[:-5] if name.endswith("Error") else name

    def _template_fields(self) -> dict[str, str]:
        return {}

    def _resolved_field(self, key: str) -> str | None:
        detail_value = self.details.get(key)
        if isinstance(detail_value, str) and detail_value:
            return detail_value
        template_value = self._template_fields().get(key)
        if isinstance(template_value, str) and template_value:
            return template_value
        return None

    def __str__(self) -> str:
        lines = [f"{type(self).__name__}: {self.message}"]
        context_lines = []
        if location := self._resolved_field("location"):
            context_lines.append(f"Location: {location}")
        if cause := self._resolved_field("cause"):
            context_lines.append(f"Cause: {cause}")
        if self.hint:
            context_lines.append(f"Hint: {self.hint}")
        if context_lines:
            lines.append("")
            lines.extend(context_lines)
        if fix_snippet := self._resolved_field("fix_snippet"):
            lines.append("")
            lines.append("Fix:")
            lines.extend(f"  {line}" for line in fix_snippet.splitlines())
        if doc := self._resolved_field("doc"):
            lines.append("")
            lines.append(f"Docs: {doc}")
        return "\n".join(lines)


class DatasourceSecretInPlaintextError(DatasourceError):
    def _template_fields(self) -> dict[str, str]:
        datasource = self.details.get("datasource")
        field_name = self.details.get("field")
        ds_ref = datasource if isinstance(datasource, str) and datasource else "<datasource>"
        field_ref = field_name if isinstance(field_name, str) and field_name else "<field>"
        env_ref = f"{field_ref}_env"
        return {
            "location": f"project datasource {ds_ref!r}",
            "cause": (
                f"field {field_ref!r} is a sensitive credential and must not be stored as a "
                "literal in the datasource file."
            ),
            "fix_snippet": (
                "import marivo.datasource as md\n"
                f'datasource = md.DatasourceSpec(name={ds_ref!r}, backend_type="...", ..., {env_ref}="<BACKEND_TYPE>_{field_ref.upper()}")\n'
                "md.datasource(datasource)\n"
                f'# e.g. export TRINO_{field_ref.upper()}="<your secret>"'
            ),
            "doc": "marivo-skills/marivo-semantic/references/datasource.md",
        }


class DatasourceFieldInvalidError(DatasourceError):
    def _template_fields(self) -> dict[str, str]:
        datasource = self.details.get("datasource")
        field_name = self.details.get("field")
        reason = self.details.get("reason")
        ds_ref = datasource if isinstance(datasource, str) and datasource else "<datasource>"
        field_ref = field_name if isinstance(field_name, str) and field_name else "<field>"
        reason_ref = reason if isinstance(reason, str) and reason else "invalid value"
        return {
            "location": f"marivo/datasources/ entry {ds_ref!r} field {field_ref!r}",
            "cause": reason_ref,
            "doc": "marivo-skills/marivo-semantic/references/datasource.md",
        }


class DatasourceLoadError(DatasourceError):
    def _template_fields(self) -> dict[str, str]:
        path = self.details.get("path")
        reason = self.details.get("reason")
        path_ref = path if isinstance(path, str) and path else "marivo/datasources/"
        reason_ref = reason if isinstance(reason, str) and reason else "invalid datasource file"
        return {
            "location": path_ref,
            "cause": reason_ref,
            "doc": "marivo-skills/marivo-semantic/references/datasource.md",
        }


class DatasourceDuplicateError(DatasourceError):
    def _template_fields(self) -> dict[str, str]:
        datasource = self.details.get("datasource")
        ds_ref = datasource if isinstance(datasource, str) and datasource else "<datasource>"
        return {
            "location": f"marivo/datasources/ entry {ds_ref!r}",
            "cause": "duplicate datasource name",
            "doc": "marivo-skills/marivo-semantic/references/datasource.md",
        }


class DatasourceMissingError(DatasourceError):
    def _template_fields(self) -> dict[str, str]:
        datasource = self.details.get("datasource")
        available = self.details.get("available")
        backend_type = self.details.get("backend_type")
        ds_ref = datasource if isinstance(datasource, str) and datasource else "<datasource>"
        bt_arg = (
            f'backend_type="{backend_type}", '
            if isinstance(backend_type, str) and backend_type
            else 'backend_type="<backend_type>", '
        )
        available_line = (
            f"datasource not found; configured datasources: {available}."
            if isinstance(available, list) and available
            else "datasource not found; no datasources are configured yet."
        )
        return {
            "location": "marivo/datasources/",
            "cause": f"datasource {ds_ref!r} is not configured; {available_line}",
            "fix_snippet": (
                "import marivo.datasource as md\n"
                "md.register(\n"
                f'    md.DatasourceSpec(name="{ds_ref}", {bt_arg}host="...", port=..., user_env="USER_VAR")\n'
                ")\n"
                "# Sensitive fields go via *_env on DatasourceSpec."
            ),
            "doc": "marivo-skills/marivo-semantic/references/datasource.md",
        }


class DatasourceSecretStorePermissionsError(DatasourceError):
    def _template_fields(self) -> dict[str, str]:
        path = self.details.get("path")
        mode = self.details.get("mode")
        path_ref = path if isinstance(path, str) and path else "~/.marivo/secrets.toml"
        mode_ref = oct(mode) if isinstance(mode, int) else "unknown"
        return {
            "location": path_ref,
            "cause": (
                f"datasource secret store permissions are {mode_ref}; "
                "the file must be readable and writable only by the current user."
            ),
            "fix_snippet": "chmod 600 ~/.marivo/secrets.toml",
            "doc": "marivo-skills/marivo-semantic/references/datasource.md",
        }


class DatasourceEnvVarMissingError(DatasourceError):
    def _template_fields(self) -> dict[str, str]:
        datasource = self.details.get("datasource")
        env_var = self.details.get("env_var")
        field_name = self.details.get("field")
        ds_ref = datasource if isinstance(datasource, str) and datasource else "unknown_datasource"
        var_ref = env_var if isinstance(env_var, str) and env_var else "UNKNOWN_SECRET_ENV"
        field_ref = field_name if isinstance(field_name, str) and field_name else "secret_field"
        return {
            "location": f"marivo/datasources/ entry {ds_ref!r} field {field_ref!r}",
            "cause": (
                f"datasource field {field_ref!r} resolves to env var {var_ref!r}, "
                "but that variable is not set in os.environ and is not present in "
                "~/.marivo/secrets.toml."
            ),
            "fix_snippet": (
                f'export {var_ref}="secret_value"\n'
                f"import marivo.datasource as md\n"
                f'md.test("{ds_ref}")  # remembers the secret after validation'
            ),
            "doc": "marivo-skills/marivo-semantic/references/datasource.md",
        }


class DatasourceBackendTypeUnsupportedError(DatasourceError):
    def _template_fields(self) -> dict[str, str]:
        backend_type = self.details.get("backend_type")
        supported = self.details.get("supported")
        bt_ref = (
            backend_type if isinstance(backend_type, str) and backend_type else "<backend_type>"
        )
        supported_line = (
            f"supported: {sorted(supported)}."
            if isinstance(supported, list | set | tuple) and supported
            else "no supported backend_type values registered."
        )
        return {
            "location": "md backend dispatch",
            "cause": f"backend_type={bt_ref!r} is not handled by datasource backend dispatch; {supported_line}",
            "doc": "marivo-skills/marivo-semantic/references/datasource.md",
        }


class DatasourceSchemaVersionError(DatasourceError):
    def _template_fields(self) -> dict[str, str]:
        got = self.details.get("got")
        expected = self.details.get("expected")
        path = self.details.get("path")
        got_ref = str(got) if got is not None else "<missing>"
        expected_ref = str(expected) if expected is not None else "<expected>"
        path_ref = path if isinstance(path, str) and path else "marivo/datasources/"
        return {
            "location": path_ref,
            "cause": (
                f"datasource registry schema_version={got_ref} is not supported by this "
                f"version of marivo.analysis (expected {expected_ref})."
            ),
            "doc": "marivo-skills/marivo-semantic/references/datasource.md",
        }


class DatasourceConnectionError(DatasourceError):
    def _template_fields(self) -> dict[str, str]:
        datasource = self.details.get("datasource")
        cause = self.details.get("cause")
        ds_ref = datasource if isinstance(datasource, str) and datasource else "<datasource>"
        cause_ref = (
            cause if isinstance(cause, str) and cause else "backend rejected the connection."
        )
        return {
            "location": f"md.test({ds_ref!r}) dial",
            "cause": cause_ref,
            "fix_snippet": (
                "# verify host/port reachability and that env_ref secrets are exported, then:\n"
                "import marivo.datasource as md\n"
                f"md.test({ds_ref!r})"
            ),
            "doc": "marivo-skills/marivo-semantic/references/datasource.md",
        }


class DatasourcePreviewError(DatasourceError):
    pass


class DatasourceMetadataError(DatasourceError):
    def _template_fields(self) -> dict[str, str]:
        datasource = self.details.get("datasource")
        table = self.details.get("table")
        ds_ref = datasource if isinstance(datasource, str) and datasource else "<datasource>"
        table_ref = table if isinstance(table, str) and table else "<table>"
        return {
            "location": f"md.inspect_table({ds_ref!r}, table={table_ref!r})",
            "cause": self.details.get("cause", "table metadata inspection failed"),
            "fix_snippet": (
                "import marivo.datasource as md\n"
                f"md.describe({ds_ref!r})\n"
                f"md.test({ds_ref!r})\n"
                f"md.inspect_table({ds_ref!r}, table={table_ref!r})"
            ),
            "doc": "marivo-skills/marivo-semantic/references/datasource.md",
        }


# Backward-compatible alias for code that still references the old name.
DatasourceConfigError = DatasourceError
