# dbts

[![PyPI](https://img.shields.io/pypi/v/dbts.svg)](https://pypi.org/project/dbts/)
[![Python](https://img.shields.io/pypi/pyversions/dbts.svg)](https://pypi.org/project/dbts/)
[![CI](https://github.com/luiul/dbts/actions/workflows/ci.yml/badge.svg)](https://github.com/luiul/dbts/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

dbt environment runner with Snowflake zero-copy clone sandboxes.

`dbts` lets you run dbt against a private, per-developer Snowflake clone of your staging or live database — without managing a separate set of credentials, profile files, or CLIs. It also acts as a single front for running dbt against your shared `dev`, `staging`, and `live` targets.

## Install

```bash
uv tool install dbts
# or run ad hoc:
uvx dbts ...
```

## Quick start

1. Add a `sandbox:` target to `~/.dbt/profiles.yml` (alongside your existing `dev`, `staging`, `live`). The database name must match the pattern `<PREFIX>_SANDBOX_<USER>`:

   ```yaml
   <your_profile_name>:           # whatever your dbt_project.yml's profile: field says
     outputs:
       sandbox:
         type: snowflake
         account: <same as the other targets>
         user: <same as the other targets>
         role: <same as the other targets>
         authenticator: externalbrowser
         database: <prefix>_sandbox_<your_username>
         warehouse: <same as the other targets>
         schema: <same as the other targets>
   ```

2. Create your clone:

   ```bash
   dbts up --from staging
   ```

3. Run dbt against it:

   ```bash
   dbts build my_model
   dbts test  +my_model+
   ```

4. Refresh or drop when done:

   ```bash
   dbts refresh --from staging
   dbts drop
   ```

## Commands

**Sandbox** — manage the per-developer zero-copy clone:

```text
dbts up        --from staging|live         create the clone
dbts refresh   --from staging|live         drop and re-create the clone
dbts status                                show clone DB, source, age, owner
dbts drop                                  drop the clone
```

**Inspect** — preview / audit before (or after) a build:

```text
dbts plan      [selectors...]              preview the build set
  --cost                                     estimate credits + runtime from QUERY_HISTORY
  --days N                                   QUERY_HISTORY lookback window (default 7, max 365)
dbts freshness [selectors...]              audit lineage; flag stale tables, suggest a rebuild
  --since                                    explicit threshold (24h, 7d, 1w, or ISO datetime)
```

**dbt pass-through** — forwarded to dbt with `--target sandbox` by default:

```text
dbts run|build|test|compile|seed|snapshot|ls|show
dbts debug|deps|source|docs|parse|clean
  --target sandbox|staging|live|dev          choose target (-t)
```

**Meta:**

```text
dbts version                               print installed version
```

Global flags: `-v / --verbose` (debug logging, including DDL), `-q / --quiet` (warnings only). `-h` is a shorthand for `--help` on every command. Run `dbts <command> --help` for the full option list.

## Selectors

Bare positional model selectors work the same as in dbt:

```bash
dbts build my_model+              # forwarded as `--select my_model+`
dbts test +my_model+              # ancestors and descendants
dbts run a b c+                   # multi-selector union
dbts build my_model+ --exclude experiments
dbts build --select a b           # `--select` + bare positional are merged
```

Internally, bare positional args on `run / build / test / compile / seed / snapshot / ls / show` are promoted to a `--select` value before being forwarded to dbt. Other subcommands (`debug`, `deps`, `docs`, `parse`, `clean`, `source`) pass arguments through verbatim.

## Previewing a build

`dbts plan` lists exactly which models a `dbts build` (or `run`/`test`/...) with the same selectors would touch. Useful when a build against `--target live` blows up halfway and you need to add `--exclude` rules.

```bash
dbts plan my_model+ another_model+ --target live           # offline, fast
dbts plan --select tag:slow --exclude path:models/intermediate
dbts plan my_model+ --target live --cost                   # + Snowflake cost breakdown
```

By default the command is offline — it parses the dbt project but never connects to Snowflake. Output groups models by directory and shows materialization, tags, and parent count per model. The footer prints suggested `--exclude path:<dir>` and `--exclude tag:<name>` snippets sized by how many models each would prune.

Pass `--cost` to also estimate Snowflake credits and runtime from `SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY`. With cost on, each per-model row gets `median run` and `last seen` columns (median execution time across recent runs of that model, whatever mode they happened to be in), and the footer adds total credits + USD for an incremental run vs a full refresh, plus a top-5 most expensive list. The default lookback is 7 days; widen with `--days 30` (max 365).

Cost estimates require your dbt project to set a structured `query_tag` containing a `model` field (HelloFresh's `set_query_tag` macro is one example) and the connecting role to read `SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY`. USD figures use $3.00 per credit by default; override with `$DBTS_CREDIT_RATE`. If access is missing, `dbts plan` prints a hint and falls back to the offline output.

## Auditing freshness after an incident

`dbts freshness` answers "did data flow correctly through this lineage?" by reading `INFORMATION_SCHEMA.TABLES.LAST_ALTERED` for every table in the selected build set, sorting topologically (parents before children), and highlighting stale links in red.

```bash
# Did the catch-up run reach everything downstream of these models? (defaults to --target live)
dbts freshness base__recipe__cps+ base__recipe__ingredient__cps+

# Audit the full lineage in both directions
dbts freshness +base__recipe__cps+

# Compare against an explicit incident timestamp
dbts freshness base__recipe__cps+ --since '2026-05-09 17:00'

# Audit your sandbox clone instead
dbts freshness base__recipe__cps+ --target sandbox
```

**Default target: `live`.** Unlike `dbts build` / `dbts plan` (which default to `sandbox`), `dbts freshness` defaults to `live` because the typical post-incident question is "did production catch up?". Pass `--target sandbox|staging|dev` to audit a different environment. The command translates the target into the actual physical database name (`<DB>_SANDBOX_<USER>`, `<DB>_STAGING`, `<DB>_DEV`, or unsuffixed for `live`), mirroring the project's `generate_database_name` macro convention.

### How "stale" is decided

Two values drive the colored output:

- **Baseline** — the most recent `LAST_ALTERED` across all tables in the build set. It's the "freshest thing you have right now," shown in the header for context.
- **Threshold** — anything older than this is flagged as stale (red row).

Without `--since`, the threshold is **adaptive**: `baseline − 6 hours`. The threshold drifts with whatever's currently fresh, so you don't have to know what "fresh" means today.

```
dbts freshness recipe+
# baseline = 14:02 (freshest table)
# threshold = 08:02   (anything older is stale)
```

With `--since`, the threshold is **explicit** and the adaptive window is ignored. Useful when you know exactly when something broke:

```bash
dbts freshness recipe+ --since '2026-05-09 17:00'   # absolute (ISO datetime or date)
dbts freshness recipe+ --since 24h                  # relative (s, m, h, d, w)
```

The footer prints a copy-pasteable `dbts build --select X+` line that covers the minimum set of stale-roots needed to catch the chain back up — building any model that's already fresh upstream is avoided.

### What `LAST_ALTERED` actually means

The signal is Snowflake's `INFORMATION_SCHEMA.TABLES.LAST_ALTERED`, which dbt bumps on every `INSERT`, `MERGE`, `UPDATE`, or `CREATE OR REPLACE` — so "fresh" means "dbt touched it recently," not necessarily "new rows arrived." For the post-incident "did the chain re-execute?" question, that's exactly the right semantic. Requires the connecting role to read `INFORMATION_SCHEMA.TABLES`.

## Project-side coupling

`dbts` assumes the dbt project's `generate_database_name` macro recognises `ENV=sandbox` and routes models into a `_SANDBOX_<USER>` suffixed database. See the dbt project's README for the macro snippet.

## Profile resolution

`dbts` resolves the dbt profile name in this order:

1. `$DBTS_PROFILE` if set.
2. The `profile:` field in `dbt_project.yml` at the project root.

Jinja `{{ env_var('NAME', 'default') }}` calls in the `profile:` field are rendered against the current environment, so projects whose profile name is templated (e.g. `tardis_{{ env_var('warehouse', 'snowflake') }}`) work out of the box.

## Development

```bash
uv sync --group dev
uv run pytest
prek install   # one-time, runs ruff + ty on every commit
```

`prek` (Astral's Rust port of `pre-commit`) is the recommended runner for the hooks defined in `.pre-commit-config.yaml`. Install it via `brew install prek` or `uv tool install prek`. The standard `pre-commit` binary works just as well if you'd rather use it.

Cutting a release (after moving `[Unreleased]` entries under a `[X.Y.Z]` heading in `CHANGELOG.md`):

```bash
./scripts/release.sh 0.6.0
```

The script bumps `pyproject.toml`, syncs the lockfile, runs the full check suite, commits, tags, pushes, and creates the GitHub release with notes pulled from `CHANGELOG.md`. See [`scripts/README.md`](scripts/README.md) for both supported workflows and recovery tips.
