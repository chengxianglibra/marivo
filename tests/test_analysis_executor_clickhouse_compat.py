"""ClickHouse 22.3 compatibility tests for SQL transformations."""

from marivo.analysis.executor.runner import _fix_clickhouse_datetrunc_case


def test_fix_clickhouse_datetrunc_hour():
    sql = "SELECT dateTrunc('HOUR', ts) FROM table"
    expected = "SELECT dateTrunc('hour', ts) FROM table"
    assert _fix_clickhouse_datetrunc_case(sql) == expected


def test_fix_clickhouse_datetrunc_minute():
    sql = "SELECT dateTrunc('MINUTE', ts) FROM table"
    expected = "SELECT dateTrunc('minute', ts) FROM table"
    assert _fix_clickhouse_datetrunc_case(sql) == expected


def test_fix_clickhouse_datetrunc_multiple():
    sql = """
    SELECT
        dateTrunc('HOUR', ts) AS hour_bucket,
        dateTrunc('DAY', ts) AS day_bucket,
        dateTrunc('MINUTE', ts) AS minute_bucket
    FROM table
    """
    result = _fix_clickhouse_datetrunc_case(sql)
    assert "dateTrunc('hour'" in result
    assert "dateTrunc('day'" in result
    assert "dateTrunc('minute'" in result
    assert "dateTrunc('HOUR'" not in result
    assert "dateTrunc('DAY'" not in result
    assert "dateTrunc('MINUTE'" not in result


def test_fix_clickhouse_datetrunc_all_units():
    """Test all time units that Ibis might generate."""
    units = ["SECOND", "MINUTE", "HOUR", "DAY", "WEEK", "MONTH", "QUARTER", "YEAR"]

    for unit in units:
        sql = f"SELECT dateTrunc('{unit}', ts) FROM table"
        result = _fix_clickhouse_datetrunc_case(sql)
        expected = f"SELECT dateTrunc('{unit.lower()}', ts) FROM table"
        assert result == expected, f"Failed for unit {unit}"


def test_fix_clickhouse_datetrunc_preserves_other_sql():
    """Ensure the transformation doesn't affect non-dateTrunc parts."""
    sql = """
    SELECT
        dateTrunc('HOUR', timestamp_col) AS bucket_start,
        count(*) AS event_count,
        AVG(value) AS avg_value
    FROM events
    WHERE status = 'ACTIVE'
    GROUP BY bucket_start
    ORDER BY bucket_start DESC
    """
    result = _fix_clickhouse_datetrunc_case(sql)

    assert "dateTrunc('hour'" in result

    assert "count(*)" in result
    assert "AVG(value)" in result
    assert "status = 'ACTIVE'" in result
    assert "GROUP BY bucket_start" in result


def test_fix_clickhouse_datetrunc_no_match():
    """Test that SQL without dateTrunc is unchanged."""
    sql = "SELECT * FROM table WHERE date > '2024-01-01'"
    assert _fix_clickhouse_datetrunc_case(sql) == sql
