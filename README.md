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

1. Add a `sandbox:` target to `~/.dbt/profiles.yml` (alongside your existing `dev`, `staging`, `live`):

   ```yaml
   tardis_snowflake:
     outputs:
       sandbox:
         type: snowflake
         account: <same as the other targets>
         user: <same as the other targets>
         role: <same as the other targets>
         authenticator: externalbrowser
         database: scm_analytics_sandbox_<your_username>
         warehouse: <same as the other targets>
         schema: raw_data_vault
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

```text
dbts up      --from staging|live   create the clone
dbts refresh --from staging|live   drop and re-create the clone
dbts status                        show clone DB, source, age
dbts drop                          drop the clone

dbts build|run|test|compile|...    pass through to dbt (default --target sandbox)
  --target sandbox|staging|live|dev   choose target

dbts version                       print installed version
```

Global flags: `-v / --verbose` (debug logging, including DDL), `-q / --quiet` (warnings only). `-h` is a shorthand for `--help`.

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

Pass `--cost` to also estimate Snowflake credits and runtime from `SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY`. With cost on, each per-model row gets `p50 incr` and `last seen` columns, and the footer adds total credits + USD for an incremental run vs a full refresh, plus a top-5 most expensive list. The default lookback is 7 days; widen with `--days 30` (max 365).

Cost estimates require your dbt project to set a structured `query_tag` containing a `model` field (HelloFresh's `set_query_tag` macro is one example) and the connecting role to read `SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY`. USD figures use $3.00 per credit by default; override with `$DBTS_CREDIT_RATE`. If access is missing, `dbts plan` prints a hint and falls back to the offline output.

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
./scripts/release.sh 0.4.0
```

The script bumps `pyproject.toml`, syncs the lockfile, runs the full check suite, commits, tags, pushes, and creates the GitHub release with notes pulled from `CHANGELOG.md`.
