"""Local connection for the synthetic example project."""

import marivo.datasource as md

md.duckdb(name="local", path="readings.duckdb")
