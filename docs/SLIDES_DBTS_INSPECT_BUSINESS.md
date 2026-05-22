---
marp: true
theme: default
paginate: true
size: 16:9
title: dbts inspect commands (business)
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

### Two tools that answer two questions

15 min · slides + live demo

---

## What is dbts?

A small command-line tool we built on top of **dbt** (the framework that runs our SQL transformations in Snowflake).

It adds a couple of helpers that answer two practical questions data engineers ask all the time, **without actually running anything heavy**.

- **`dbts plan`** — *if I run this, what will happen and what will it cost?*
- **`dbts freshness`** — *did the pipeline actually deliver data?*

> Both are **read-only**. They look at the warehouse and tell you what's going on; they never change anything. Safe to run on production.

---

## Today's focus

`dbts` has more commands than these two. We're skipping the rest:

- **Sandbox commands** (creating personal copies of the database) — they need rework to fit our current ADR / governance around access privileges.
- **dbt pass-through commands** (the standard build / test / run commands) — those are for engineers maintaining the project.

Today is just about the two **inspection** commands above.

---

<!-- _class: lead -->

# Part 1

## `dbts plan`

If I run this, what will happen — and what will it cost?

> Live demo follows.

---

## What `dbts plan` does, in plain English

Before you actually run a build, `dbts plan` tells you:

1. **Which tables would be rebuilt** — grouped by area (e.g. "marts/finance", "data vault"), so you can spot if your filter is too broad.
2. **Roughly how long it would take** — based on how long the same tables took on previous runs.
3. **Roughly what it would cost** — in Snowflake credits and dollars.

Think of it as a **dry run with a price tag**. You decide whether to actually run the build, narrow the scope, or wait until off-peak.

> The cost figure is also a **before/after** for optimization work — run `dbts plan --cost` before and after a refactor, and you can see whether the change actually got cheaper.

---

## Where the cost numbers come from

`dbts plan --cost` doesn't guess. It looks at **how long each table took the last few times it was built** (median of recent runs), reads which Snowflake warehouse size was used, and converts that into credits and dollars.

- **Default lookback**: last 7 days (configurable).
- **Currency conversion**: Snowflake's standard rate of $3 per credit (overridable).
- **Estimate, not budget**: numbers are based on past performance. Treat them as **cents-vs-dollars** signals, not exact billing.

> Sometimes a table has only been built incrementally and never fully rebuilt. In that case `dbts plan` makes an educated guess for the full-rebuild cost (multiplies the incremental time) and clearly marks those rows as estimates so you don't over-trust them.

---

<!-- _class: lead -->

# Part 2

## `dbts freshness`

Did the pipeline actually deliver data?

> Live demo follows.

---

## What `dbts freshness` does, in plain English

It picks a part of the pipeline (e.g. "everything feeding the recipe dashboards") and tells you, table by table, **when each one last received data**.

- **Green**: this table was updated recently.
- **Red**: this table is behind. Either nothing ran, or it ran but had no data to write.

Sorted in pipeline order: source tables at the top, downstream marts at the bottom. So if you see red breaking out at a certain layer, **that's where the chain stopped flowing**.

> The most common use case: **after an incident**. The on-call engineer fixed the source. Did the fix actually propagate all the way to the dashboard the business team uses? `dbts freshness` answers that in one command.

---

## What "stale" really means

A table is flagged **stale** when Snowflake hasn't recorded any new data in it within the threshold (default: about 6 hours behind the freshest table in the set).

Two ways a table ends up stale:

1. **The pipeline didn't run.** Job failed, was paused, never scheduled.
2. **The pipeline ran, but had nothing new to write.** The upstream is quiet — no new orders, no new SKUs, etc. Common with incremental tables.

> Both cases show up the same way (red), and they're often the **same problem from a business perspective**: "the report I expected today doesn't have today's data." The footer of the command suggests the smallest rebuild command to catch the chain back up.

---

## Takeaways

- **`dbts plan`** answers: *what will this build touch, and roughly what will it cost?* Useful before kicking off a long run, and as a before/after on optimization work.
- **`dbts freshness`** answers: *did the pipeline actually deliver data end-to-end?* Useful after incidents and for routine "is the dashboard up to date?" checks.
- Both commands are **read-only and safe**. They observe; they don't change anything.
- Numbers are **estimates from recent activity**, not exact billing or guarantees. Good for decisions, not for invoices.

---

<!-- _class: lead -->

# Demo + Q&A
