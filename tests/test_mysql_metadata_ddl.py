from __future__ import annotations

import re
import unittest

from app.storage.schema import MYSQL_METADATA_DDL, metadata_ddl_for_backend


def _columns_from_constraint(columns_sql: str) -> list[str]:
    return [part.strip().split()[0] for part in columns_sql.split(",") if part.strip()]


class MySQLMetadataDDLTests(unittest.TestCase):
    def test_mysql_ddl_omits_sqlite_only_syntax(self) -> None:
        ddl = "\n".join(metadata_ddl_for_backend("mysql"))

        forbidden = (
            "datetime('now')",
            "AUTOINCREMENT",
            "sqlite_master",
            "PRAGMA",
            "WHERE identity_key != ''",
            "BEGIN",
            "RAISE(ABORT)",
            "CREATE INDEX IF NOT EXISTS",
        )
        for token in forbidden:
            self.assertNotIn(token, ddl)

    def test_mysql_ddl_contains_marker_timestamp_and_mysql_table_options(self) -> None:
        ddl = "\n".join(MYSQL_METADATA_DDL)

        self.assertIn("CREATE TABLE IF NOT EXISTS metadata_schema_marker", ddl)
        self.assertIn("DEFAULT CURRENT_TIMESTAMP(6)", ddl)
        self.assertIn("VARCHAR(128) PRIMARY KEY", ddl)
        self.assertIn("ENGINE=InnoDB DEFAULT CHARSET=utf8mb4", ddl)
        self.assertIn("CHECK (backend IN ('sqlite', 'mysql'))", ddl)

    def test_mysql_partial_identity_unique_contract_uses_generated_column(self) -> None:
        ddl = "\n".join(MYSQL_METADATA_DDL)

        self.assertIn("identity_key_unique", ddl)
        self.assertIn("GENERATED ALWAYS AS (NULLIF(identity_key, '')) STORED", ddl)
        self.assertIn(
            "CREATE UNIQUE INDEX idx_propositions_session_type_identity "
            "ON propositions(session_id, proposition_type, identity_key_unique)",
            ddl,
        )

    def test_mysql_latest_active_metric_unique_contract_uses_generated_column(self) -> None:
        ddl = "\n".join(MYSQL_METADATA_DDL)

        self.assertIn("metric_latest_active_ref", ddl)
        self.assertIn(
            "GENERATED ALWAYS AS (CASE WHEN status = 'published' AND is_latest_active = 1 "
            "THEN metric_ref ELSE NULL END) STORED",
            ddl,
        )
        self.assertIn(
            "CREATE UNIQUE INDEX idx_semantic_metric_contracts_latest_active "
            "ON semantic_metric_contracts(metric_latest_active_ref)",
            ddl,
        )

    def test_mysql_foreign_keys_are_explicit_table_constraints(self) -> None:
        ddl = "\n".join(MYSQL_METADATA_DDL)

        self.assertIn(
            "ALTER TABLE source_objects ADD CONSTRAINT fk_source_objects_datasource_id "
            "FOREIGN KEY (datasource_id) REFERENCES datasources(datasource_id)",
            ddl,
        )
        self.assertIn(
            "ALTER TABLE step_metadata ADD CONSTRAINT fk_step_metadata_step_id "
            "FOREIGN KEY (step_id) REFERENCES steps(step_id) ON DELETE CASCADE",
            ddl,
        )

        inline_references = [
            line
            for statement in MYSQL_METADATA_DDL
            for line in statement.splitlines()
            if "REFERENCES" in line and not line.strip().startswith("ALTER TABLE")
        ]
        self.assertEqual(inline_references, [])

    def test_mysql_key_columns_are_not_large_text_types(self) -> None:
        column_types: dict[str, dict[str, str]] = {}
        key_columns: list[tuple[str, list[str]]] = []
        for statement in MYSQL_METADATA_DDL:
            table_match = re.match(r"CREATE TABLE IF NOT EXISTS (\w+) \(", statement.strip())
            if table_match:
                table = table_match.group(1)
                column_types[table] = {}
                for line in statement.splitlines()[1:]:
                    column_match = re.match(
                        r"\s*(\w+)\s+([A-Z]+(?:\(\d+\))?|LONGTEXT|TEXT)",
                        line,
                    )
                    if column_match:
                        column_types[table][column_match.group(1)] = column_match.group(2)
                    constraint_match = re.match(r"\s*(?:UNIQUE|PRIMARY KEY)\s*\((.*)\)", line)
                    if constraint_match:
                        key_columns.append(
                            (table, _columns_from_constraint(constraint_match.group(1)))
                        )
                continue

            index_match = re.match(
                r"CREATE (?:UNIQUE )?INDEX \w+ ON (\w+)\((.*)\)",
                statement.strip(),
                re.S,
            )
            if index_match:
                table = index_match.group(1)
                key_columns.append((table, _columns_from_constraint(index_match.group(2))))

        offenders = [
            f"{table}.{column}:{column_types.get(table, {}).get(column)}"
            for table, columns in key_columns
            for column in columns
            if column_types.get(table, {}).get(column) in {"TEXT", "LONGTEXT"}
        ]
        self.assertEqual(offenders, [])

    def test_mysql_large_text_columns_do_not_use_defaults(self) -> None:
        offenders = [
            line.strip()
            for statement in MYSQL_METADATA_DDL
            for line in statement.splitlines()
            if re.search(r"\b(?:TEXT|LONGTEXT)\b.*\bDEFAULT\b", line)
        ]

        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
