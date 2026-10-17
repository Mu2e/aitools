---
name: coding-with-query-engine
description: Build and troubleshoot Mu2e Query Engine (QE) URL reads for PostgreSQL-backed conditions and operations data. Use when composing QE URLs, doing Python HTTP reads, or implementing C++ database fetches with Offline DbService DbReader/DbId helpers.
compatibility: Requires Mu2e Offline environment, network access to FNAL QE endpoints, and familiarity with Offline/DbService and conditions table naming
metadata:
  version: "1.1.0"
  last-updated: "2026-10-10"
---

# Coding with Query Engine

## Overview

Use this skill for Mu2e code that reads PostgreSQL table data through Fermilab Query Engine (QE) over HTTP.

Primary usage modes:

- Build QE query URLs and validate query semantics
- Read data in Python via HTTP GET
- Read data in C++ using Mu2e DbService wrappers

For Mu2e C++ applications, do **not** use the official QE C API directly. Use Mu2e wrappers centered on:

- `Offline/DbService/inc/DbReader.hh`
- `Offline/DbService/inc/DbIdList.hh`
- `Offline/DbTables/inc/DbId.hh`
- `Offline/DbService/inc/DbCurl.hh`

Reference docs:

- https://github.com/fermisda/qengine/wiki/Query-Engine
- https://mu2ewiki.fnal.gov/wiki/ConditionsDbCode#URL_format

---

## QE URL Model (REST)

Base endpoint pattern:

```text
<prefix>/query?dbname=<db>&t=<table>[&c=<cols>][&w=<cond>...][&o=<sort>][&l=<limit>][&f=<fmt>][&x=<cacheCtl>]
```

Core parameters:

- `dbname` (required): logical database name
- `t` (required): table name (`schema.table` when needed)
- `c` (optional): comma-separated column list, default all columns
- `w` (optional, repeatable): where terms, typically `column:op:value`
- `o` (optional): sort columns, prefix `-` for descending
- `l` (optional): row limit
- `f` (optional): output format (`csv` default, `text` supported)
- `x` (optional): cache control (`yes`, `no`, `clear`)

Comparison operators for `w`: `lt`, `le`, `eq`, `ne`, `ge`, `gt`.

That list is exhaustive, lower-case, and **must not be guessed at** — see
[Unsupported Operators Fail Silently](#unsupported-operators-fail-silently)
below. There is no `in`, no `like`, no `between`, and `EQ` is not `eq`.

Example semantics:

```text
w=cid:eq:2&w=channel:le:1
```

means SQL-like AND conditions.

---

## Unsupported Operators Fail Silently

QE parses each `w` term as `col:op:val`. When `op` is not one of the six
operators above, QE does **not** report an error — it falls back to implicit
equality and treats the whole remainder as the literal value, building
`WHERE col = 'op:val'`.

What that costs you depends entirely on the column's type:

| Column type | Result | Visibility |
|---|---|---|
| `integer`, `numeric`, `timestamp` | Postgres cannot cast `'op:val'` → Application error, HTTP 500 | **Loud** — you find out immediately |
| `text` / `varchar` | `col = 'op:val'` is a valid comparison that matches nothing → 0 rows, HTTP 200 | **Silent** — reads as "no such data" |

Verified against `mu2e_dqm_prd`:

```text
w=sid:bogusop:5          -> HTTP 500: invalid input syntax for type integer: "bogusop:5"
                            LINE 1: ... where sid = 'bogusop:5'          <- shows the mechanism
w=end_time:bogusop:...   -> HTTP 500: invalid input syntax for type timestamp with time zone
w=process:bogusop:valNightly -> HTTP 200, header only, zero rows, no warning
w=process:like:valN%     -> HTTP 200, zero rows (like is unsupported)
w=process:EQ:valNightly  -> HTTP 200, zero rows (operators are case-sensitive)
w=sid:in:5,6             -> HTTP 500 (in is unsupported; int column makes it loud)
```

The dangerous cell is the text column: a typo'd or invented operator produces a
clean, successful, empty response that is indistinguishable from a genuine
absence of data. Any code that reports "no rows found" as a conclusion must not
be able to reach that state by accident.

### Guard: validate operators at clause-construction time

Never interpolate a caller-supplied operator into a `w` term unchecked. Build
clauses through a helper that rejects anything outside the known set, so a bad
operator raises in your own code instead of becoming a silent empty result:

```python
QE_OPERATORS = frozenset({"lt", "le", "eq", "ne", "ge", "gt"})


def where(column: str, op: str, value) -> str:
    """Build one QE `w` term, rejecting operators QE would silently ignore.

    QE treats an unrecognized operator as part of the VALUE of an implicit
    equality, so `col:bogus:v` becomes `col = 'bogus:v'` -- an error on a
    typed column, but a silent zero-row result on a text one. Validating
    here converts that into an exception at the call site.
    """
    if op not in QE_OPERATORS:
        raise ValueError(
            f"unsupported QE operator {op!r}; QE accepts only "
            f"{sorted(QE_OPERATORS)} and silently returns no rows for anything "
            f"else on a text column. Note there is no 'in' or 'like'."
        )
    return f"{column}:{op}:{value}"
```

Apply the same rule in C++ before pushing terms into a `DbReader::StringVec`.

Two corollaries worth carrying into any QE client:

- An empty result is only authoritative if every operator in the query was
  validated. Otherwise "zero rows" and "malformed query" are the same response.
- Prefer the bare `col:value` form for equality over `col:eq:value` when the
  operator is not a variable — there is no operator to get wrong.

---

## Cache vs No-Cache Behavior

Mu2e relies on two QE URL heads per database connection:

- cache URL (`...:8444/.../query?`): uses web cache for scale
- nocache URL (`...:9443/.../query?`): bypasses cache and reads DB directly

In Mu2e connection config (`Offline/DbService/data/connections.txt`), each database has both endpoints.

Important behavior:

- Use cache for high-rate stable table reads
- Use no-cache when latest DB entries must be discovered immediately
- Non-cache reads bypass cache and do not refresh cached entries

C++ `DbReader` behavior details:

- Non-`val*` tables use cache by default (`setUseCache(true)`)
- `val*` IoV metadata tables default to no-cache for freshness
- `setCacheLifetime(n)` can provide bounded caching for `val*` queries via a rolling URL token

---

## Mu2e Database Names (from connections)

Known logical database names in current config include:

- `mu2e_conditions_prd`
- `mu2e_conditions_dev`
- `mu2e_dqm_prd`
- `dcs_archive`
- `mu2e_tracker_prd`
- `run_info`

Always pass the logical name via `dbname=<name>`.

---

## Standard Workflow

### 1) Start from a concrete query goal

Define precisely:

- target logical database (`dbname`)
- target table (`t`)
- required columns (`c`)
- filter predicates (`w`)
- sort/limit (`o`, `l`)
- freshness requirement (cache vs no-cache)

### 2) Validate URL shape with curl

```bash
curl -s "https://dbdata0vm.fnal.gov:8444/QE/mu2e/prod/app/SQ/query?dbname=mu2e_conditions_prd&t=val.tables&c=tid,name&o=tid" | head
```

Force direct DB read:

```bash
curl -s "https://dbdata0vm.fnal.gov:9443/QE/mu2e/prod/app/SQ/query?dbname=mu2e_conditions_prd&t=val.tables&c=tid,name&o=tid" | head
```

### 3) Implement in Python or C++

- Python: compose URL with proper query encoding and repeat `w`
- C++: use `DbIdList` + `DbReader` and pass select/table/where/order pieces

### 4) Confirm result shape and freshness

- Verify header/content format (`csv` default)
- Check row count and ordering
- Re-run against cache and no-cache endpoints when freshness matters

---

## Example: cached read for `run_info` / `test_sc.run`

Use this when you need enough detail to identify the `run` table definition.

1) Confirm tables in schema `test_sc`:

```bash
curl -s "https://dbdata0vm.fnal.gov:8444/QE/mu2e/prod/app/SQ/I/tables?dbname=run_info&ns=test_sc&f=json"
```

2) Get `run` table column definition (names/types):

```bash
curl -s "https://dbdata0vm.fnal.gov:8444/QE/mu2e/prod/app/SQ/I/columns?dbname=run_info&t=test_sc.run&f=json"
```

3) Optional: probe a few rows from the table using cache endpoint:

```bash
curl -s "https://dbdata0vm.fnal.gov:8444/QE/mu2e/prod/app/SQ/query?dbname=run_info&t=test_sc.run&c=*&l=5"
```

4) If the definition appears stale, compare against no-cache endpoint:

```bash
curl -s "https://dbdata0vm.fnal.gov:9443/QE/mu2e/prod/app/SQ/I/columns?dbname=run_info&t=test_sc.run&f=json"
```

Notes:

- For table definition, prefer `/I/columns` over `/query`.
- Use fully qualified table name `test_sc.run`.
- `f=json` is easiest for parsing column metadata programmatically.
- In current QE deployment, `/I/tables?...&f=csv` may return a server error; use `f=json` for schema introspection.

---

## Python Pattern

Use URL parameter encoding rather than manual string concatenation.

```python
from urllib.parse import urlencode
from urllib.request import urlopen

base = "https://dbdata0vm.fnal.gov:9443/QE/mu2e/prod/app/SQ/query?"
params = [
    ("dbname", "mu2e_conditions_prd"),
    ("t", "tst.calib1"),
    ("c", "flag,dtoe"),
    ("w", "cid:eq:2"),
    ("w", "channel:le:1"),
    ("o", "-flag"),
    ("f", "csv"),
]
url = base + urlencode(params)

with urlopen(url, timeout=30) as response:
    payload = response.read().decode("utf-8")

print(payload)
```

Guidelines:

- Keep `params` as ordered tuples so repeated `w` keys are preserved
- Use timeout and explicit error handling for production code
- Prefer no-cache endpoint when checking for just-inserted rows

---

## C++ Pattern (Mu2e wrappers)

Use `DbIdList` to resolve logical DB name to endpoint URLs, then query through `DbReader`.

```cpp
#include "Offline/DbService/inc/DbIdList.hh"
#include "Offline/DbService/inc/DbReader.hh"

mu2e::DbIdList idList;
mu2e::DbId id = idList.getDbId("mu2e_conditions_prd");

mu2e::DbReader reader;
reader.setDbId(id);
reader.setUseCache(true);
reader.setAbortOnFail(false);

std::string csv;
mu2e::DbReader::StringVec where;
where.emplace_back("cid:eq:2");
where.emplace_back("channel:le:1");

int rc = reader.query(csv, "flag,dtoe", "tst.calib1", where, "-flag");
if (rc != 0) {
  auto err = reader.lastError();
}
```

Key API notes:

- `query(csv, select, table, where, order)` maps directly to URL pieces
- `multiQuery(...)` reuses socket/handle for multiple related reads
- `setAbortOnFail(false)` lets caller handle failures instead of throwing
- Returned CSV from `DbReader` has header line removed by default

Do not switch to QE official C API for Mu2e Offline code paths unless specifically required by external integration.

---

## Query Composition Rules

1. Keep table and column names exact and lower-case where required by schema.
2. Build each `w` clause as `field:op:value` unless using implicit `eq` form,
   and validate `op` against the six supported operators first — an
   unsupported one is silently absorbed into the value on a text column (see
   [Unsupported Operators Fail Silently](#unsupported-operators-fail-silently)).
3. Repeat `w` for AND logic; avoid embedding SQL text.
4. Use deterministic ordering for reproducible comparisons.
5. Prefer explicit columns over `*` in production code.
6. Separate freshness checks (no-cache) from steady-state reads (cache).

---

## Troubleshooting

### Empty or unexpected results

- **Check every `w` operator first.** On a text column, an unsupported or
  misspelled operator returns zero rows under HTTP 200 with no error, which
  looks exactly like absent data. Only `lt`, `le`, `eq`, `ne`, `ge`, `gt`
  exist, lower-case. See
  [Unsupported Operators Fail Silently](#unsupported-operators-fail-silently).
- Verify `dbname`, schema/table name, and column names
- Test same URL with no-cache endpoint
- Remove filters incrementally, then re-add one by one — if a filter's removal
  changes an empty result to a populated one, suspect its operator before its
  value
- Re-run the suspect clause against an `integer` or `timestamp` column: a bad
  operator that is silent on text turns into a loud HTTP 500 there, which names
  the problem outright

### Intermittent failures or gateway errors

- Retry with bounded backoff
- For C++, inspect `reader.lastError()` and return code
- Increase `DbReader` timeout only when justified

### Fresh data not visible

- Confirm you are using no-cache endpoint (or `x=no` if supported)
- For Mu2e C++, disable cache with `setUseCache(false)` where appropriate
- Re-check `val*` table behavior and any cache lifetime settings

---

## Safety and Performance Guardrails

- Keep queries narrow (`c`, `w`, `l`) to reduce payload and latency
- Avoid repeated broad scans from grid jobs
- Default to cached reads for stable data at scale
- Use direct DB reads sparingly and intentionally
- Log canonical URL components (without secrets) when debugging

---

## Output Template

When asked to produce QE access code, return:

```text
Goal:
- <what data is needed and why>

Chosen endpoint mode:
- cache|nocache
- reason: <freshness vs scale>

Query components:
- dbname: <...>
- table: <...>
- columns: <...>
- where:
  - <w1>
  - <w2>
- order: <...>
- limit: <...>

Implementation:
- Language: Python|C++
- Code: <minimal runnable snippet>

Validation:
- curl URL used:
- row-count or sample rows:
- cache-vs-nocache comparison result:
```

---

## References

- [Query Engine](https://github.com/fermisda/qengine/wiki/Query-Engine)
- [Mu2e ConditionsDbCode URL format](https://mu2ewiki.fnal.gov/wiki/ConditionsDbCode#URL_format)
- `Offline/DbService/inc/DbReader.hh`
- `Offline/DbService/src/DbReader.cc`
- `Offline/DbService/inc/DbIdList.hh`
- `Offline/DbService/src/DbIdList.cc`
- `Offline/DbService/data/connections.txt`
