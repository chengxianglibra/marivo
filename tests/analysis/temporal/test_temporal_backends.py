"""Temporal authority failures and precision use independent bounded expectations."""

import ibis.expr.types as ir


def bucket_start_expr(value: ir.Value, grain) -> ir.Value:
    seconds = grain.count * {"hour": 3600, "minute": 60}[grain.unit]
    offset = ((value.hour() * 3600 + value.minute() * 60 + value.second()) // seconds) * seconds
    return value.truncate("D") + offset.as_interval("s")
