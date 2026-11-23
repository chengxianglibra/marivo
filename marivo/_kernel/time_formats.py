"""Canonical source-time encodings shared by datasource and semantic compilers."""

# Canonical, fixed-width encodings whose native order is chronological.
# Values declare valid dates/clocks, zero padding and exactly these separators.
ORDERED_TIME_FORMATS: dict[str, int] = {
    "%Y%m%d": 86400,
    "%Y-%m-%d": 86400,
    "%Y%m%d%H": 3600,
    "%Y-%m-%d %H": 3600,
    "%Y%m%d%H%M": 60,
    "%Y-%m-%d %H:%M": 60,
    "%Y%m%d%H%M%S": 1,
    "%Y-%m-%d %H:%M:%S": 1,
}
