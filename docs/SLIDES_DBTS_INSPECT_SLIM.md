---
marp: true
theme: default
paginate: true
size: 16:9
title: dbts inspect commands (slim)
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

  section.lead { text-align: center; }
---

<!-- _class: lead -->

# dbts

### Inspect commands, slim cut

15 min · slides + live demo

---

## What is dbts, and why

A small Python CLI that wraps `dbt`, plus two read-only inspection commands.

It answers three questions without leaving your terminal:

1. **What will this build touch?** ( `dbts plan <selectors>` )
2. **How much will it cost?** ( `dbts plan ... --cost` )
3. **Did data actually flow through the lineage?** ( `dbts freshness <selectors>` )

> Today's focus is the two **Inspect** commands. **Sandbox** commands (`up`, `refresh`, `drop`, `publish`, ...) are skipped — they need rework to fit the current ADR / governance around clone privileges. **dbt pass-through** (`run`, `build`, `test`, ...) is also skipped — that's a developer/maintainer concern.

---

<!-- _class: lead -->

# Part 1

## `dbts plan`

What will this build touch, and how much will it cost?

> Live demo follows.

---

## Options

Wraps `dbt ls --output json --resource-type model`. **Offline by default**.

| Flag | Default | Notes |
| --- | --- | --- |
| `--target / -t` | `sandbox` | Profile target used to parse the project. |
| `--cost` | off | Adds Snowflake cost columns + footer. |
| `--days N` | `7` | `QUERY_HISTORY` lookback for `--cost`. 1 to 365. |

Plus standard `dbt ls` flags (`--select`, `--exclude`, `--selector`, ...). Build-only flags like `--full-refresh` and `--threads` are stripped, so a working `dbts build` invocation can be reused verbatim.

```bash
dbts plan tag:hourly                              # offline preview
dbts plan +my_model+ --target live --cost         # live cost estimate
dbts plan +my_model+ --cost --days 30             # wider lookback window
dbts plan tag:hourly --exclude path:models/intermediate
```

---

## Output

```text
models/marts/finance (4 models)
  model         materialization   tags     parents   median run   last seen
  fct_orders    incremental       hourly   3         2.4 min      3h ago
  dim_customer  table             daily    2         18.0s        1d ago

Build set: 12 models across 5 directories
  table         7
  incremental   4
  view          1

Estimated cost (last-7d median, warehouse MEDIUM)
  Incremental :  12.4 min   0.83 credits  ~$2.49   (10/12 models)
  Full refresh:  1h 38m     6.55 credits  ~$19.65  (12/12, ~7 extrapolated)

Top 5 most expensive (on full-refresh basis)
    fct_orders                   19.2 min   1.28 credits  ~$3.84
  * dim_customer                  2.4 min   0.16 credits  ~$0.48
  * extrapolated from incremental p50

Quick excludes:
  ( 4 models) --exclude path:models/marts/finance
```

---

## How values are estimated

Source: `SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY`, filtered by:

- model name from a structured `query_tag` (HelloFresh's `set_query_tag` macro)
- `query_tag:target` matches `--target` (sandbox runs don't pollute live estimates)
- successful CTAS / INSERT / MERGE / CREATE_TABLE only

Per (model, mode): `MEDIAN(elapsed_s)`, `MAX(start_time)`, warehouse size.

```python
# src/dbts/cost.py
WAREHOUSE_CREDITS = {"X-SMALL": 1, "SMALL": 2, "MEDIUM": 4, ... "6X-LARGE": 512}
credits = WAREHOUSE_CREDITS[size] * (p50_elapsed_s / 3600.0)
usd     = credits * credit_rate                  # $DBTS_CREDIT_RATE, default 3.00
```

> Missing full-refresh history? Extrapolated from `incremental_p50 × 8`. Coverage line and `*` markers in the top-5 make extrapolations auditable.

---

<!-- _class: lead -->

# Part 2

## `dbts freshness`

Did data flow through this lineage end to end?

> Live demo follows.

---

## Options

Reads `INFORMATION_SCHEMA.TABLES.LAST_ALTERED` for every table in the build set, sorts topologically, highlights stale rows in red.

| Flag | Default | Notes |
| --- | --- | --- |
| `--target / -t` | `live` | Resolves the physical DB: `_STAGING`, `_DEV`, `_SANDBOX_<USER>`, or unsuffixed for live. |
| `--since` | adaptive | Relative (`24h`, `7d`, `1w`), ISO datetime, or ISO date. Overrides the adaptive window. |

Plus the usual selector flags (`--select`, `--exclude`, `--selector`, ...).

```bash
dbts freshness +my_model+                              # default: audit live
dbts freshness +my_model+ --since '2026-05-09 17:00'   # tied to incident
dbts freshness +my_model+ --since 24h --target staging # relative + non-default target
```

---

## Output

```text
Lineage freshness check (12 models, skipped 2 ephemerals,
                         baseline 2026-05-22 14:02 UTC,
                         threshold = baseline - 6h)

  model         database.schema   last altered            ago      rows
  base__order   MYDB.LANDING      2026-05-22 14:02 UTC    just now 1.2M
  stg_order     MYDB.STAGING      2026-05-22 13:55 UTC    7m ago   1.2M
  dim_customer  MYDB.MARTS        2026-05-22 03:11 UTC    11h ago  480K   ← red
  fct_orders    MYDB.MARTS        (missing)               —        —      ← red

Stale: 2/12 models. Fresh: 10/12.

Stale tables:
  dim_customer
  fct_orders

Suggested build (covers all stale tables):
  dbts build --select dim_customer+ fct_orders+
```

---

## How "stale" is decided

Two values drive the colors:

- **Baseline**: most recent `LAST_ALTERED` in the build set. Freshest thing you have.
- **Threshold**: anything older is stale.

Default threshold is **adaptive**: `baseline - 6h`. Override with `--since` (explicit threshold, adaptive window ignored).

`LAST_ALTERED` advances when dbt actually **writes data**: `CREATE OR REPLACE` (table/view) always, `MERGE` / `INSERT` only when rows change. So "stale" can mean **the chain didn't run** *or* **it ran but had nothing to write** — common for incremental models with quiet upstreams.

> **Suggested build** picks the **minimum stale roots** (a stale model whose parents in the build set are not stale), so `+` propagation catches up downstream without redundant rebuilds.

---

## Takeaways

- **`plan`** tells you *what a build will touch* and *roughly what it will cost* before you run it.
- **`freshness`** tells you *whether the lineage actually ran end to end* — useful after an incident or to confirm a catch-up worked.
- Both commands are **read-only**. Safe to run on production; nothing gets created, dropped, or modified.
- Cost numbers are **estimates** based on past runs. Treat them as ballpark figures (cents vs. dollars), not a budget.

---

<!-- _class: lead -->

# Demo + Q&A

`dbts plan --help` · `dbts freshness --help`

`src/dbts/plan.py` · `src/dbts/freshness.py` · `src/dbts/cost.py`
