---
marp: true
theme: default
paginate: true
size: 16:9
title: dbts inspect commands
author: Luis Aceituno
footer: "dbts inspect commands"
style: |
  section {
    font-size: 26px;
    padding: 56px 72px;
  }
  section h1 { font-size: 1.9em; }
  section h2 { font-size: 1.4em; margin-bottom: 0.5em; }
  section h3 { font-size: 1.1em; }
  section pre, section code { font-size: 0.85em; }
  section table { font-size: 0.92em; }
  section li { margin: 0.25em 0; }

  /* Centered title slide */
  section.lead { text-align: center; }
---

<!-- _class: lead -->

# dbts

### Inspect commands deep dive

15 min

---

## What you'll leave with

- A clear picture of **what `dbts plan` and `dbts freshness` actually do**.
- The **flags that matter** on each, and what they accept.
- A reading guide for **every column and footer line**, so the output is unambiguous.
- A working mental model of **how the cost and staleness numbers are estimated** (so you can tell when to trust them).

---

## What is dbts?

A small Python CLI that wraps `dbt`, plus two read-only inspection commands.

```text
Sandbox          per-developer Snowflake zero-copy clone
Inspect          plan, freshness                            (today's focus)
dbt pass-through run, build, test, compile, ls, ...         (skipped)
Meta             version
```

> Pip-installable (`uv tool install dbts`). Resolves the dbt profile from
> `$DBTS_PROFILE` or `dbt_project.yml`.

---

## Why it's useful

Three questions it answers without leaving your terminal:

1. **What will this build touch?** ( `dbts plan <selectors>` )
2. **How much will it cost in credits and dollars?** ( `dbts plan ... --cost` )
3. **Did data actually flow through the lineage?** ( `dbts freshness <selectors>` )

> Both Inspect commands are **read-only**. They wrap `dbt ls` and read warehouse metadata. They never mutate state.

---

## Out of scope today

**Sandbox commands** (`up`, `refresh`, `drop`, `status`, `publish`, `unpublish`).

- They require Snowflake privileges (CREATE DATABASE, OWNERSHIP on a clone) that not every engineer has on this project.
- The design needs reworking to be compliant with the current ADR / governance around clone ownership.
- A separate session once the rework lands.

**dbt pass-through commands** (`run`, `build`, `test`, `compile`, `ls`, ...).

- Relevant for developers and maintainers of the dbt project itself, not for this audience.
- They behave the same as `dbt`, just with a default target.

---

## Agenda

1. **Part 1**: `dbts plan`, options and output, how cost is estimated.
2. **Part 2**: `dbts freshness`, options and output, how staleness is decided.
3. **Takeaways**.

---

<!-- _class: lead -->

# Part 1

## `dbts plan`

What will this build touch, and how much will it cost?

---

## What `dbts plan` does

Wraps `dbt ls --output json --resource-type model`, parses the JSON, and prints:

- Per-directory tables of the matched models.
- A footer summary: total count, materialization breakdown, suggested `--exclude` snippets.
- (Optional) Snowflake cost estimate from `QUERY_HISTORY`.

**Offline by default**. No Snowflake call unless `--cost` is passed.

> Reference: `src/dbts/plan.py`. The flag-stripping logic lives in `_dbt_ls.py`'s `strip_ls_incompatible`, so a working `dbts build` invocation can be reused verbatim (`--full-refresh`, `--threads`, etc. are dropped before `dbt ls` sees them).

---

## Options

| Flag | Default | Notes |
| --- | --- | --- |
| `--target / -t` | `sandbox` | Picks the dbt profile target (parses the project under that env). |
| `--cost` | off | Adds Snowflake cost columns + footer (one extra Snowflake query). |
| `--days N` | `7` | `QUERY_HISTORY` lookback when `--cost` is on. Range 1 to 365. |

Plus everything `dbt ls` accepts: `--select`, `--exclude`, `--selector`, `--vars`, `--resource-type`, etc.

```bash
dbts plan tag:hourly --exclude path:models/intermediate
dbts plan +my_model+ --target live --cost --days 14
```

---

## Output anatomy (offline)

```text
models/marts/finance (4 models)
  model                materialization   tags             parents
  fct_orders           incremental       hourly,critical  3
  dim_customer         table             daily            2
  ...

Build set: 12 models across 5 directories
  table          7
  incremental    4
  view           1

Quick excludes: (copy-paste into your dbts build command)
  ( 4 models) --exclude path:models/marts/finance
  ( 6 models) --exclude tag:slow
```

Each row maps 1:1 to a `dbt ls` JSON record. `parents` counts only **upstream models in the build set** (other resource types are excluded from the count).

---

## `--cost` adds two columns and a footer block

```text
models/marts/finance (4 models)
  model         materialization   tags         parents   median run   last seen
  fct_orders    incremental       hourly       3         2.4 min      3h ago
  dim_customer  table             daily        2         18.0s        1d ago

Estimated cost (last-7d median, warehouse MEDIUM)
  Incremental :   12.4 min   0.83 credits  ~$2.49  (10/12 models)
  Full refresh:    1h 38m    6.55 credits  ~$19.65 (12/12 models, ~7 extrapolated)

Top 5 most expensive (on full-refresh basis)
    fct_orders                              19.2 min   1.28 credits  ~$3.84
    *  dim_customer                          2.4 min   0.16 credits  ~$0.48
  * extrapolated from incremental p50
```

> `median run` and `last seen` come from per-model p50 of past runs. `*` rows in the top-5 are extrapolations, not measurements.

---

## How cost is estimated, part 1: where the numbers come from

`SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY`, filtered by:

- `start_time > now - <days>`
- `execution_status = 'SUCCESS'`
- `query_type IN ('CREATE_TABLE_AS_SELECT', 'INSERT', 'MERGE', 'CREATE_TABLE')`
- `query_tag` matches one of the build-set models (prefiltered with `LIKE`, then parsed as JSON)
- **`query_tag:target` matches `--target`** (so a `--target sandbox --cost` call doesn't pull in live's history)

Per (model, mode) pair, dbts records: `MEDIAN(elapsed_s)`, `MAX(start_time)`, `MAX(warehouse_size)`, `COUNT(*)`.

> Requires the dbt project to set a structured `query_tag` with a `model` field. HelloFresh's `set_query_tag` macro is one example. If the tag is missing, the cost block falls back to "no matching query history".

---

## How cost is estimated, part 2: credits and dollars

Credits per warehouse-hour, by size:

```python
WAREHOUSE_CREDITS = {
    "X-SMALL": 1, "SMALL": 2, "MEDIUM": 4, "LARGE": 8,
    "X-LARGE": 16, "2X-LARGE": 32, "3X-LARGE": 64,
    "4X-LARGE": 128, "5X-LARGE": 256, "6X-LARGE": 512,
}
```

Per model:

```python
credits = WAREHOUSE_CREDITS[size] * (p50_elapsed_s / 3600.0)
usd     = credits * credit_rate     # $DBTS_CREDIT_RATE, default 3.00
```

> Source of truth: `src/dbts/cost.py`. Override the rate with `export DBTS_CREDIT_RATE=4.50` if your contract differs from the public list price.

---

## Incremental vs full refresh: when there's no full-refresh history

A model's history may have only incremental runs (full refreshes are rare in steady state). When that happens:

```python
FULL_REFRESH_MULTIPLIER = 8.0
# full_refresh_elapsed_s ≈ incremental_p50 * 8
```

The footer makes this auditable:

- `coverage` line: `(10/12 models, ~7 extrapolated)`.
- Top-5: extrapolated rows are prefixed with `*`.
- Models with **zero** history at all are counted under `models_no_data` (skipped from totals).

> Crude on purpose. The point is "is this build going to cost cents or dollars?", not a precise budget.

---

<!-- _class: lead -->

# Part 2

## `dbts freshness`

Did data flow through this lineage end to end?

---

## What `dbts freshness` does

Reads `INFORMATION_SCHEMA.TABLES.LAST_ALTERED` for every table in the selected build set, sorts topologically (parents before children), and highlights stale tables in red.

```bash
# Default: audit live (post-incident "did production catch up?")
dbts freshness +my_model+

# Explicit threshold tied to an incident timestamp
dbts freshness +my_model+ --since '2026-05-09 17:00'

# Audit your sandbox clone instead
dbts freshness +my_model+ --target sandbox
```

> Reference: `src/dbts/freshness.py`. Ephemeral models compile to CTEs (no warehouse table), so they're skipped and reported in the header count.

---

## Options

| Flag | Default | Notes |
| --- | --- | --- |
| `--target / -t` | `live` | Resolves to the **physical** DB name via the project's convention (see next slide). |
| `--since` | (adaptive) | Accepts relative (`24h`, `7d`, `1w`), ISO datetime, or ISO date. When set, the adaptive window is ignored. |

Plus the usual `--select`, `--exclude`, `--selector`, etc., shared with `dbts plan`.

> Default target is `live` because the typical question after an incident is "did production catch up?". `plan` defaults to `sandbox`; `freshness` defaults to `live`.

---

## Resolving the physical database

`dbt_project.yml` lists static `database:` values. The actual warehouse DB depends on the env, exactly mirroring the project's `generate_database_name` macro:

| `--target` | Postfix | Example |
| --- | --- | --- |
| `live` | (none) | `MYDB` |
| `staging` | `_STAGING` | `MYDB_STAGING` |
| `dev` | `_DEV` | `MYDB_DEV` |
| `sandbox` | `_SANDBOX_<USER>` | `MYDB_SANDBOX_LACEITUNO` |

`_SENSITIVE` suffixes are preserved (postfix slots **before** `_SENSITIVE`).

> Without this, `--target sandbox` would silently audit live, which is the opposite of what you want.

---

## Output anatomy

```text
Lineage freshness check (12 models, skipped 2 ephemerals,
                         baseline 2026-05-22 14:02 UTC,
                         threshold = baseline - 6h)

  model               database.schema     last altered            ago      rows
  base__order         MYDB.LANDING        2026-05-22 14:02 UTC    just now  1.2M
  stg_order           MYDB.STAGING        2026-05-22 13:55 UTC    7m ago    1.2M
  dim_customer        MYDB.MARTS          2026-05-22 03:11 UTC    11h ago   480K   ← red
  fct_orders          MYDB.MARTS          (missing)               —         —      ← red

Stale: 2/12 models. Fresh: 10/12.

Stale tables:
  dim_customer
  fct_orders

Suggested build (covers all stale tables):
  dbts build --select dim_customer+ fct_orders+
```

---

## How "stale" is decided

Two quantities drive the colored output:

- **Baseline**: the most recent `LAST_ALTERED` across all tables in the build set. The "freshest thing you have right now."
- **Threshold**: anything older than this is flagged red.

Without `--since`, the threshold is **adaptive**: `baseline - 6h`. So the bar drifts with whatever's currently fresh; you don't need to know what "fresh" means today.

With `--since`, the threshold is **explicit** and the adaptive window is ignored:

```bash
dbts freshness recipe+ --since '2026-05-09 17:00'   # ISO datetime or date
dbts freshness recipe+ --since 24h                  # relative: s, m, h, d, w
```

---

## What `LAST_ALTERED` actually means

Snowflake updates `INFORMATION_SCHEMA.TABLES.LAST_ALTERED` whenever a table is **modified**:

- `CREATE OR REPLACE` (table / view) — **always** bumps it.
- `MERGE` / `INSERT` / `UPDATE` / `DELETE` — bumps it **only when rows actually change**. A `MERGE` that matches zero source rows doesn't.

That has a real consequence for `dbts freshness`:

| dbt did this... | Result | Stale? |
| --- | --- | --- |
| Rebuilt a `table` / `view` model | `LAST_ALTERED` advances | fresh |
| `MERGE` that wrote new/changed rows | `LAST_ALTERED` advances | fresh |
| `MERGE` with **zero matching source rows** | no change | **stale** (even though dbt ran) |
| Run failed / didn't execute | no change | stale |

So "stale" is a stronger signal than "didn't run" — it means **"this table didn't get any new data."** Common cause: an `incremental` model with a quiet upstream (e.g. an `is_incremental()` watermark filter that selected nothing today).

> If you need "did dbt run at all", check the orchestrator logs. If you need "did rows arrive", check source freshness. `dbts freshness` is the middle question: **"did data make it into the table?"**

---

## Suggested rebuild: minimum stale roots

The footer prints a copy-pasteable `dbts build --select X+ Y+ ...` line.

The set is built from **stale roots only**, where a stale root is:

> A stale model whose parents (within the build set) are NOT stale.

Rationale: building each stale root with `+` propagates downstream automatically and avoids redundant rebuilds of tables that are already fresh upstream. So you catch up the chain with the smallest valid command.

```text
Stale: dim_customer, fct_orders, fct_orders_summary
       └── stale roots: dim_customer, fct_orders
       └── fct_orders_summary is downstream of fct_orders, not a root

Suggested build:
  dbts build --select dim_customer+ fct_orders+
```

---

## Takeaways

- **`dbts plan`** answers *what will this build touch* and *how much will it cost*. Offline by default, opts in to a Snowflake `QUERY_HISTORY` query with `--cost`.
- **`dbts freshness`** answers *did data actually flow through the lineage*, by reading `INFORMATION_SCHEMA.TABLES.LAST_ALTERED`. Defaults to `live`. Adaptive 6 h threshold, override with `--since`.
- Both commands are **read-only** wrappers around `dbt ls`. Both honor the project's `generate_database_name` convention. Both are safe to run on production.
- Cost numbers are **estimates from medians**, with extrapolation from incremental p50 × 8 when full-refresh history is missing. The footer tells you how many models were extrapolated; trust accordingly.

---

<!-- _class: lead -->

# Q&A

`dbts plan --help` · `dbts freshness --help`

`src/dbts/plan.py` · `src/dbts/freshness.py` · `src/dbts/cost.py`
