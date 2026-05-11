# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.5.0] — 2026-05-11

### Added
- `dbts plan` can now estimate Snowflake credits and elapsed time for an incremental run vs. a full refresh of the build set, based on `SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY` filtered by the `query_tag:model` field. Models with no full-refresh history are extrapolated from incremental p50 × 8.
- `--cost` flag on `dbts plan`: off by default (offline, no Snowflake call). When passed, the per-model table gets `median run` and `last seen` columns, and the footer adds total credits/USD for incremental vs. full refresh plus a top-5 most expensive list.
- `--days N` flag on `dbts plan` (default 7) controls the QUERY_HISTORY lookback window. 7 days covers a typical week of dev iteration and is roughly 4x faster than the prior 30-day query; pass `--days 30` (or up to 365) when investigating long-term trends.
- Per-directory tables now render with column headers (`model | materialization | tags | parents`, plus `median run | last seen` when `--cost` is on) so the columns are self-documenting.
- `$DBTS_CREDIT_RATE` env var (default `3.00` USD/credit) overrides the credit-to-dollar conversion.
- `dbts plan` now strips `dbt build`-only flags (`--full-refresh`, `--threads`, `--fail-fast`, etc.) before invoking `dbt ls`, so a working `dbts build` invocation can be reused verbatim.
- `tests/test_cost.py` covering credit math, duration formatting, env-var handling, and aggregation logic (25 new tests).

### Performance
- `dbts plan --cost` now opens the Snowflake connection in parallel with `dbt ls` (saves a few seconds on every run).
- The QUERY_HISTORY query uses a `query_tag LIKE` prefilter so Snowflake can prune rows before parsing JSON, materially faster on large account-usage tables.

### Fixed
- `_promote_selectors` no longer silently turns trailing positionals into `--select` when the user typed `--exclude foo bar`. Bare positionals now attach to the most recent selector flag (`--select` / `-s` / `--exclude`), matching the user's likely intent. Regression tests added.

### Repo polish
- `.github/dependabot.yml` — weekly auto-PRs for GitHub Actions and Python dependencies (grouped per ecosystem).
- `.editorconfig` — consistent indent/whitespace/encoding across editors.
- CI now runs `uv lock --check` before sync, so a stale `uv.lock` fails fast.
- `pyproject.toml` adds `Changelog` and `Releases` URLs to the PyPI sidebar.

### Notes
- Cost estimates require the dbt project to set a structured `query_tag` containing a `model` field (the HelloFresh `set_query_tag.sql` macro is one example). Projects without it will see the "no matching query history" message.
- The user's role must be able to read `SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY`. If access is denied, `dbts plan` falls back to its previous output with a yellow hint and continues.

## [0.4.0] — 2026-05-11

### Added
- Pytest suite covering pure-logic helpers: selector promotion, env_var rendering, SQL identifier/literal quoting, sandbox-name pattern, `dbt ls` JSON parsing, and exclude suggestions (51 tests).
- `.pre-commit-config.yaml` with ruff format / ruff check / ty hooks. Compatible with `prek` (Astral's Rust port of `pre-commit`).
- `scripts/release.sh` — one-command release helper that bumps the version, syncs the lockfile, runs the full check suite, tags, pushes, and creates the GitHub release with notes pulled from `CHANGELOG.md`.
- `CHANGELOG.md` (this file).

### Changed
- CI now runs on a Python 3.11 / 3.12 / 3.13 matrix and includes a `pytest` step. Confirms the `requires-python = ">=3.11"` claim with three real runs per push/PR.
- `pyproject.toml`: `pytest>=8` added to the `dev` dependency group; `[tool.pytest.ini_options]` block configures testpaths and CLI flags.

## [0.3.0] — 2026-05-11

### Added
- `dbts plan <selectors>` — preview the build set for a `dbts build` (or run/test/...) invocation without connecting to Snowflake.
  - Wraps `dbt ls --output json --resource-type model`.
  - Output groups models by directory; columns: model name, materialization, tags, parent count.
  - Footer prints copy-pasteable `--exclude path:<dir>` and `--exclude tag:<name>` snippets sized by how many models each would prune.
- `README.md` "Previewing a build" section.

### Changed
- `dbts.dbt_runner` exposes `ensure_dbt_on_path()`, `dbt_env()`, `project_root()` so `plan.py` can reuse the env-building logic without duplicating it.

## [0.2.0] — 2026-05-08

### Breaking
- Removed the hardcoded `tardis_snowflake` profile default. `dbts` now resolves the profile from `$DBTS_PROFILE`, falling back to the `profile:` field in `dbt_project.yml`. Jinja `{{ env_var(...) }}` calls in that field are rendered against the current environment.

### Added
- `--verbose / -v` and `--quiet / -q` global flags routed through stdlib `logging` + `RichHandler`. DDL is now logged at debug level.
- `-h` shorthand for `--help` on every command.
- `dbts.__version__` importable via `from dbts import __version__`.
- Bare positional model selectors are auto-promoted to `--select` for `run / build / test / compile / seed / snapshot / ls / show` (e.g. `dbts build my_model+` works).
- Yellow "hint:" lines on common `ConfigError`s (missing profile, sandbox missing, dbt not on PATH, etc).
- README badges (PyPI, Python, CI, License); CI workflow running ruff + ty on push/PR.

### Fixed
- DDL in `dbts up / refresh / drop` now double-quotes Snowflake database identifiers, so unusual names no longer break clone operations.

## [0.1.0] — 2026-05-07

- Initial release on PyPI.
- Sandbox lifecycle (`up`, `refresh`, `drop`, `status`) over Snowflake zero-copy clones.
- Pass-through wrapper for `dbt run / build / test / compile / debug / seed / snapshot / ls / deps / source / docs / parse / show / clean`.
- Profile resolution from `~/.dbt/profiles.yml`.

[Unreleased]: https://github.com/luiul/dbts/compare/v0.5.0...HEAD
[0.5.0]: https://github.com/luiul/dbts/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/luiul/dbts/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/luiul/dbts/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/luiul/dbts/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/luiul/dbts/releases/tag/v0.1.0
