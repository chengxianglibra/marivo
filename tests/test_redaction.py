from __future__ import annotations

import unittest

from marivo.redaction import redact_mapping, redact_sensitive_text, redact_url


class RedactionTests(unittest.TestCase):
    def test_redacts_url_passwords_and_assignments(self) -> None:
        text = "failed mysql://marivo:secret@db.example/marivo password=secret token:abc123"

        redacted = redact_sensitive_text(text)

        self.assertNotIn("secret", redacted)
        self.assertNotIn("abc123", redacted)
        self.assertIn("mysql://marivo:***@db.example/marivo", redacted)
        self.assertIn("password=***", redacted)
        self.assertIn("token:***", redacted)

    def test_redacts_python_mapping_repr_secrets(self) -> None:
        redacted = redact_sensitive_text(
            "{'dsn': 'mysql://marivo:secret@db.example/marivo', 'password': 'secret'}"
        )

        self.assertNotIn("secret", redacted)
        self.assertIn("'dsn': ***", redacted)
        self.assertIn("'password': ***", redacted)

    def test_redacts_nested_sensitive_mapping_fields(self) -> None:
        redacted = redact_mapping(
            {
                "host": "db.example",
                "password": "secret",
                "ssl": {"ca": "/ca.pem", "cert_secret": "cert"},
            }
        )

        self.assertEqual(redacted["host"], "db.example")
        self.assertEqual(redacted["password"], "***")
        self.assertEqual(redacted["ssl"], {"ca": "/ca.pem", "cert_secret": "***"})

    def test_redact_url_preserves_non_secret_parts(self) -> None:
        redacted = redact_url("mysql+pymysql://marivo:secret@db.example:3307/marivo?ssl=true")

        self.assertEqual(redacted, "mysql+pymysql://marivo:***@db.example:3307/marivo?ssl=true")


if __name__ == "__main__":
    unittest.main()
