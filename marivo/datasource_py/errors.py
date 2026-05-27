"""Datasource configuration errors shared by datasource and analysis APIs."""

from __future__ import annotations

from typing import Any


class DatasourceConfigError(Exception):
    """Base class for project datasource configuration errors."""

    def __init__(
        self,
        *,
        message: str,
        hint: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.details = details or {}

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


class DatasourceSecretInPlaintextError(DatasourceConfigError):
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
                "import marivo.datasource_py as md\n"
                f'md.datasource(name={ds_ref!r}, backend_type="...", ..., {env_ref}="MY_SECRET_VAR")\n'
                f'# then: export MY_SECRET_VAR="<your secret>"'
            ),
            "doc": "marivo-skill/marivo-py-semantic/references/datasource.md",
        }


class DatasourceFieldInvalidError(DatasourceConfigError):
    def _template_fields(self) -> dict[str, str]:
        datasource = self.details.get("datasource")
        field_name = self.details.get("field")
        reason = self.details.get("reason")
        ds_ref = datasource if isinstance(datasource, str) and datasource else "<datasource>"
        field_ref = field_name if isinstance(field_name, str) and field_name else "<field>"
        reason_ref = reason if isinstance(reason, str) and reason else "invalid value"
        return {
            "location": f".marivo/datasource entry {ds_ref!r} field {field_ref!r}",
            "cause": reason_ref,
            "doc": "marivo-skill/marivo-py-semantic/references/datasource.md",
        }


class DatasourceLoadError(DatasourceConfigError):
    def _template_fields(self) -> dict[str, str]:
        path = self.details.get("path")
        reason = self.details.get("reason")
        path_ref = path if isinstance(path, str) and path else ".marivo/datasource"
        reason_ref = reason if isinstance(reason, str) and reason else "invalid datasource file"
        return {
            "location": path_ref,
            "cause": reason_ref,
            "doc": "marivo-skill/marivo-py-semantic/references/datasource.md",
        }


class DatasourceDuplicateError(DatasourceConfigError):
    def _template_fields(self) -> dict[str, str]:
        datasource = self.details.get("datasource")
        ds_ref = datasource if isinstance(datasource, str) and datasource else "<datasource>"
        return {
            "location": f".marivo/datasource entry {ds_ref!r}",
            "cause": "duplicate datasource name",
            "doc": "marivo-skill/marivo-py-semantic/references/datasource.md",
        }
