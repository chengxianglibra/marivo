"""Local connection for the synthetic example project."""

import marivo.datasource as md

md.duckdb(wall_clock_timezone="UTC", name="local", path="readings.duckdb")
