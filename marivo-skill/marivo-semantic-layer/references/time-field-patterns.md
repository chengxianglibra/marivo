# Time Field data_type, format, and required_prefix Decision Guide

Every time field (`dimension.is_time: true`) must carry a MARIVO custom extension with these
properties:

| Property | Required | When |
|----------|----------|------|
| `support_min_granularity` | always | every time field |
| `data_type` | always | every time field |
| `format` | when `data_type` is `"string"` or `"integer"` | string/integer partition columns |
| `required_prefix` | when `format` is `"hh"` or `"h"` | hour-only fields that pair with a date field |

## Inferring data_type from browse_columns

When `browse_columns` returns a column, map its SQL type to the OSI `data_type`:

| SQL type from browse_columns | OSI `data_type` |
|------------------------------|-----------------|
| `date` | `"date"` |
| `timestamp`, `timestamp with time zone`, `timestamptz`, `datetime` | `"timestamp"` |
| `varchar`, `text`, `char`, `nvarchar`, `string` | `"string"` |
| `integer`, `int`, `bigint`, `smallint`, `tinyint` | `"integer"` |

Normalize variant spellings (case-insensitive) to the four OSI values above.

## Inferring format from preview_table sample values

When `data_type` is `"string"` or `"integer"`, inspect sample values from `preview_table` to
determine the `format`:

### String data_type

| Sample value | Length | `format` | Notes |
|---|---|---|---|
| `'20260325'` | 8 chars | `"yyyymmdd"` | Date partition |
| `'2026-03-25'` | 10 chars | `"yyyy-mm-dd"` | ISO date partition |
| `'2026032514'` | 10 chars | `"yyyymmddhh"` | Hour-precision single column |
| `'20260325-14'` | 11 chars | `"yyyymmdd-hh"` | Alternative hour-precision |
| `'2026-03-25-14'` | 13 chars | `"yyyy-mm-dd-hh"` | ISO-style hour-precision |
| `'20260325T14'` | 11 chars | `"yyyymmddthh"` | T-separated hour-precision |
| `'14'` or `'03'` | 1-2 chars | `"hh"` | Hour-only, **requires `required_prefix`** |

### Integer data_type

| Sample value | Magnitude | `format` | Notes |
|---|---|---|---|
| `20260325` | 8 digits | `"yyyymmdd"` | Integer date partition |
| `14` or `3` | 0-23 | `"h"` | Hour-only, **requires `required_prefix`** |
| `1711344000` | ~1.7 billion | `"epoch_seconds"` | Unix epoch seconds |
| `18836` | ~18-25 thousand | `"epoch_days"` | Days since Unix epoch |

When the integer magnitude is ambiguous (e.g., a 5-digit value could be `epoch_days` or a
non-temporal ID), use the knowledge base or ask for clarification.

## The five time field layout patterns

### Pattern 1: Native DATE column

Physical column: `order_date DATE`

No `format` needed because the SQL engine handles date arithmetic natively.

```json
{
  "name": "order_date",
  "expression": { "dialects": [{ "dialect": "ANSI_SQL", "expression": "order_date" }] },
  "dimension": { "is_time": true },
  "custom_extensions": [{
    "vendor_name": "MARIVO",
    "data": { "support_min_granularity": "day", "data_type": "date" }
  }]
}
```

### Pattern 2: Native TIMESTAMP column

Physical column: `created_at TIMESTAMP`

No `format` needed. `support_min_granularity` is typically `"hour"` for timestamps.

```json
{
  "name": "created_at",
  "expression": { "dialects": [{ "dialect": "ANSI_SQL", "expression": "created_at" }] },
  "dimension": { "is_time": true },
  "custom_extensions": [{
    "vendor_name": "MARIVO",
    "data": { "support_min_granularity": "hour", "data_type": "timestamp" }
  }]
}
```

### Pattern 3: String date partition

Physical column: `log_date VARCHAR` storing `'20260325'`

The string encodes a date in a non-native format, so `format` is required.

```json
{
  "name": "log_date",
  "expression": { "dialects": [{ "dialect": "ANSI_SQL", "expression": "log_date" }] },
  "dimension": { "is_time": true },
  "custom_extensions": [{
    "vendor_name": "MARIVO",
    "data": { "support_min_granularity": "day", "data_type": "string", "format": "yyyymmdd" }
  }]
}
```

### Pattern 4: Single hour-precision column

Physical column: `log_hour VARCHAR` storing `'2026032514'`

One column encodes both date and hour. `format` captures the combined pattern.

```json
{
  "name": "log_hour",
  "expression": { "dialects": [{ "dialect": "ANSI_SQL", "expression": "log_hour" }] },
  "dimension": { "is_time": true },
  "custom_extensions": [{
    "vendor_name": "MARIVO",
    "data": { "support_min_granularity": "hour", "data_type": "string", "format": "yyyymmddhh" }
  }]
}
```

### Pattern 5: Composite date + hour partitions

Physical columns: `log_date VARCHAR` storing `'20260325'` + `log_hour VARCHAR` storing `'14'`

Two separate columns form a composite time axis. The hour-only field declares its dependency on the
date field via `required_prefix`. Both must be time fields in the same dataset.

Date field:

```json
{
  "name": "log_date",
  "expression": { "dialects": [{ "dialect": "ANSI_SQL", "expression": "log_date" }] },
  "dimension": { "is_time": true },
  "custom_extensions": [{
    "vendor_name": "MARIVO",
    "data": { "support_min_granularity": "day", "data_type": "string", "format": "yyyymmdd" }
  }]
}
```

Hour field:

```json
{
  "name": "log_hour",
  "expression": { "dialects": [{ "dialect": "ANSI_SQL", "expression": "log_hour" }] },
  "dimension": { "is_time": true },
  "custom_extensions": [{
    "vendor_name": "MARIVO",
    "data": {
      "support_min_granularity": "hour",
      "data_type": "string",
      "format": "hh",
      "required_prefix": "log_date"
    }
  }]
}
```

Marivo auto-discovers the composite pair from `required_prefix` and generates combined predicates
for hour-grain analysis. At day/week/month grain, only the date field is used.

If `log_hour` is an integer column storing `14` instead of a string `'14'`, use `data_type:
"integer"` and `format: "h"`.

## Recognizing composite date + hour from datasource metadata

Detect composite patterns when browsing columns:

1. `browse_columns` shows a date-like column (name contains `log_date`, `event_date`, `dt`, `date`,
   `day`) and an hour-like column (name contains `log_hour`, `event_hour`, `hour`, `hr`) in the
   same table.
2. `preview_table` confirms the hour column contains small values in range 0-23 (as strings or
   integers).
3. Model as composite: both are time fields, the hour field gets `format: "hh"` (or `"h"` if
   integer) and `required_prefix` set to the date field name.

Do NOT model the hour-only column as a standalone time field without `required_prefix` — Marivo
cannot resolve hour-grain analysis without the companion date column.

## validation error codes to watch for

When `validate_osi_semantic_models` reports these issues, the fix is to add the missing field:

| Error code | Missing field | Fix |
|---|---|---|
| Schema required-field violation | `data_type` | Add `data_type` to the MARIVO field extension |
| `MISSING_TIME_FIELD_FORMAT` | `format` | Add `format` when data_type is string or integer |
| `MISSING_REQUIRED_PREFIX` | `required_prefix` | Add `required_prefix` when format is hh or h |
| `INVALID_REQUIRED_PREFIX_FORMAT` | N/A | Remove `required_prefix` from a non-hour-only field |
| `REQUIRED_PREFIX_FIELD_NOT_FOUND` | N/A | Point `required_prefix` to an existing time field in the same dataset |
