---
marp: true
theme: default
paginate: true
size: 16:9
title: dbts inspect commands (minimal)
author: Luis Aceituno
footer: "dbts inspect commands"
style: |
  section {
    font-size: 28px;
    padding: 56px 72px;
  }
  section h1 { font-size: 1.9em; }
  section h2 { font-size: 1.4em; margin-bottom: 0.5em; }
  section h3 { font-size: 1.1em; }
  section pre, section code { font-size: 0.85em; }
  section table { font-size: 0.95em; }
  section li { margin: 0.3em 0; }

  section.lead { text-align: center; }
---

<!-- _class: lead -->

# dbts

### Two tools, two questions

15 min · slides + demo

---

## What is dbts?

A small CLI on top of **dbt** (our SQL transformation framework on Snowflake).

Adds two helpers that answer two practical questions, without running anything heavy:

- **`dbts plan`** — *what will happen, and what will it cost?*
- **`dbts freshness`** — *did the pipeline deliver data?*

> Both are **read-only**. Safe on production.

---

## Today's focus

Skipping:

- **Sandbox commands** — privilege / governance rework pending.
- **dbt pass-through** — engineer concern.

Just the two inspection commands above.

---

<!-- _class: lead -->

# Part 1

## `dbts plan`

What will happen — and what will it cost?

> Demo follows.

---

## What it does

A **dry run with a price tag**. Before you build:

1. **Which tables** would be rebuilt.
2. **Roughly how long** it would take.
3. **Roughly what it costs** — credits and dollars.

> Also a useful before/after when optimizing — did the refactor actually get cheaper?

---

## Where the numbers come from

Median of recent runs in Snowflake's query history (last 7 days, configurable). Combined with the warehouse size and the standard rate of $3/credit.

- **Estimate, not budget** — cents vs. dollars signal.
- Tables never fully rebuilt are extrapolated (and clearly marked).

---

<!-- _class: lead -->

# Part 2

## `dbts freshness`

Did the pipeline deliver data?

> Demo follows.

---

## What it does

For a chosen slice of the pipeline, table by table:

- 🟢 **updated recently**
- 🔴 **behind**

Sorted in pipeline order. Red breaking out at a layer = **that's where the chain stopped flowing.**

> Typical use: post-incident — did the fix propagate all the way to the dashboard?

---

## What "stale" means

Two ways a table ends up red:

1. **The pipeline didn't run** — failed, paused, never scheduled.
2. **It ran, but had nothing to write** — quiet upstream, no new rows.

> From the business side, both look the same: *"today's report doesn't have today's data."*

---

## Takeaways

- **`plan`** — *scope + cost*, before you run.
- **`freshness`** — *did data flow*, after the fact.
- **Read-only and safe** on production.
- Numbers are **estimates**, not invoices.

---

<!-- _class: lead -->

# Demo + Q&A
