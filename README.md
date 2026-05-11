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

`dbts plan` lists exactly which models a `dbts build` (or `run`/`test`/...) with the same selectors would touch — without connecting to Snowflake or running anything. Useful when a build against `--target live` blows up halfway and you need to add `--exclude` rules.

```bash
dbts plan my_model+ another_model+ --target live
dbts plan --select tag:slow --exclude path:models/intermediate
```

The output groups models by directory, shows materialization and tags per model, and prints a footer of suggested `--exclude path:<dir>` and `--exclude tag:<name>` snippets so you can copy-paste them straight into the corresponding `dbts build` invocation.

## Project-side coupling

`dbts` assumes the dbt project's `generate_database_name` macro recognises `ENV=sandbox` and routes models into a `_SANDBOX_<USER>` suffixed database. See the dbt project's README for the macro snippet.

## Profile resolution

`dbts` resolves the dbt profile name in this order:

1. `$DBTS_PROFILE` if set.
2. The `profile:` field in `dbt_project.yml` at the project root.

Jinja `{{ env_var('NAME', 'default') }}` calls in the `profile:` field are rendered against the current environment, so projects whose profile name is templated (e.g. `tardis_{{ env_var('warehouse', 'snowflake') }}`) work out of the box.
