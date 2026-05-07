# dbts

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
```

## Project-side coupling

`dbts` assumes the dbt project's `generate_database_name` macro recognises `ENV=sandbox` and routes models into a `_SANDBOX_<USER>` suffixed database. See the dbt project's README for the macro snippet.
