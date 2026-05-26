# Contributing to `dbts`

This guide is for people working on the `dbts` codebase itself. If you only want to use the CLI, the top-level [`README.md`](../README.md) covers install, commands, and selectors.

The audience here is a new contributor (or returning maintainer) who needs to know:

1. What the tool is and how the code is laid out.
2. How to set up locally and run the dev loop.
3. How to ship a change, from edit to merged commit.
4. How a release is cut and how it reaches PyPI.

## What `dbts` is

`dbts` is a dbt environment runner with Snowflake zero-copy clone sandboxes. It manages a per-developer clone of staging or live (`<PREFIX>_SANDBOX_<USER>`) and acts as a single front for `dev`, `staging`, `live`, and `sandbox` targets, so users don't manage a separate dbt profile or CLI per environment.

The package is small (~2.3k lines across `src/dbts/`), depends on `snowflake-connector-python`, `pyyaml`, `typer`, and `rich`, and supports Python 3.11 / 3.12 / 3.13.

## Repository map

| Path | Purpose |
| --- | --- |
| `src/dbts/` | The package. One module per command surface (see below). |
| `tests/` | pytest suite. Roughly one file per command surface. |
| `scripts/` | Maintainer scripts. Currently just `release.sh`. See [`scripts/README.md`](../scripts/README.md). |
| `docs/` | Marp slide decks and this guide. |
| `.github/workflows/` | `ci.yml` (lint / typecheck / tests on push and PR), `publish.yml` (PyPI on GitHub release). |
| `CHANGELOG.md` | Keep a Changelog format. `release.sh` parses this to build the GitHub release notes. |
| `pyproject.toml` | Single source of truth for version, deps, and ruff / ty / pytest config. |

## Module map

Each module under `src/dbts/` owns one slice of the CLI:

| Module | Responsibility |
| --- | --- |
| `cli.py` | Typer app entrypoint. Defines the `Sandbox`, `Inspect`, `dbt pass-through`, and `Meta` panels and routes each command to the modules below. |
| `clone.py` | Sandbox lifecycle (`up`, `refresh`, `drop`, `status`). Issues the zero-copy clone DDL. |
| `publish.py` | `publish` / `unpublish`. Grants or revokes read access on the sandbox for `ROLE PUBLIC`. |
| `plan.py` | `dbts plan`. Wraps `dbt ls`, groups models by directory, prints exclude hints. With `--cost`, joins `cost.py`. |
| `cost.py` | `QUERY_HISTORY` math for `dbts plan --cost` (credits, USD, p50 runtime, top-5). |
| `freshness.py` | `dbts freshness`. Reads `INFORMATION_SCHEMA.TABLES.LAST_ALTERED`, sorts topologically, suggests a rebuild set. |
| `dbt_runner.py` | Pass-through to the user's `dbt` binary. Injects `--target` and the right env. |
| `_dbt_ls.py` | Shared helpers for commands that wrap `dbt ls` (`plan`, `freshness`). |
| `snowflake.py` | Snowflake connector setup, retry policy, platform-detection silencing. |
| `config.py` | Profile resolution (`$DBTS_PROFILE`, then `profile:` in `dbt_project.yml`) with Jinja `env_var` rendering. |
| `log.py` | stdlib `logging` configured with `RichHandler`. Wires `--verbose` / `--quiet`. |

## Local setup

One-time, from a fresh clone:

```bash
uv sync --group dev                                  # install runtime + dev deps
prek install                                         # install the pre-commit hook (matches CI)
uv tool install --from . -e dbts --force --reinstall # editable CLI on $PATH
uv run pytest                                        # baseline: should pass
```

`prek` is Astral's Rust port of `pre-commit`; install via `brew install prek` or `uv tool install prek`. The standard `pre-commit` binary works equally well.

To switch back to the published version of `dbts` later: `uv tool install dbts --force --reinstall`.

## Day-to-day loop

While iterating:

```bash
uv run pytest          # tests
uv run ruff format .   # auto-format
uv run ruff check .    # lint
uv run ty check        # typecheck
```

`prek` runs ruff format, ruff check (with `--fix`), and ty on staged Python files at commit time, so the manual invocations above are mostly for tightening the inner loop while editing.

## Shipping a change

There are two flows depending on who you are.

### Solo maintainer (trunk-based)

The default branch is `main` and the maintainer commits directly to it.

1. Edit. Run the day-to-day loop until tests, lints, and typecheck are green.
2. Add or update a bullet under `## [Unreleased]` in `CHANGELOG.md` (in the same change, or a follow-up).
3. Commit using a conventional-commit prefix: `feat:`, `fix:`, `refactor:`, `chore:`, `docs:`, `style:`, `perf:`, `ci:`, or `test:`. Keep the subject short.
4. `git push origin main`. CI (`ci.yml`) runs the 3.11 / 3.12 / 3.13 matrix on every push.

### External contributors (branch + PR)

1. Fork the repo, or branch off `main`.
2. Make the change. Run the day-to-day loop locally.
3. Add or update a bullet under `## [Unreleased]` in `CHANGELOG.md`.
4. Open a PR. CI must pass (`uv lock --check`, `uv sync`, ruff format check, ruff check, ty check, pytest, all on the 3.11 / 3.12 / 3.13 matrix).
5. The maintainer reviews and merges.

### Worked example

A small feature like `dbts publish` (shipped in 0.7.0) typically lands as one to three commits:

1. Implementation in a new or existing module under `src/dbts/`.
2. Wiring in `cli.py` (the Typer command and its panel).
3. Tests in `tests/test_<thing>.py` and a `### Added` bullet under `## [Unreleased]` in `CHANGELOG.md`.

## Cutting a release

Releases are one command:

```bash
./scripts/release.sh 0.9.0
```

That script does everything end to end. Here's what actually happens, so you can debug it when something goes sideways. (The full step list and recovery tips live in [`scripts/README.md`](../scripts/README.md); the summary below is enough to follow along.)

1. **Pre-flight gate.** Must be on `main`, working tree clean, tag `v<version>` doesn't already exist, and `CHANGELOG.md` has a `## [<version>]` heading. The script then runs `git pull --ff-only origin main`, refusing to release if local has diverged.
2. **Bump.** `sed`-edits `version = "..."` in `pyproject.toml`, then `uv sync --group dev` refreshes `uv.lock`. Idempotent: if the version is already at the target (e.g. you bumped during development), the bump is a no-op.
3. **Local CI gate.** Runs the same four checks `ci.yml` runs (`ruff format --check`, `ruff check`, `ty check`, `pytest`), locally first. Avoids tagging a broken commit.
4. **Commit + tag + push.** Stages `pyproject.toml uv.lock CHANGELOG.md` and commits as `chore: release v<version>` (skipped if the working tree is already at the target). Then `git tag v<version>` and `git push origin main v<version>`.
5. **GitHub release.** `awk` extracts the `## [<version>]` block from `CHANGELOG.md` and calls `gh release create v<version> --notes "$notes"`. Falls back to GitHub auto-generated notes if the extracted block is empty.
6. **PyPI publish.** Publishing the GitHub release fires the `release: types: [published]` trigger on `.github/workflows/publish.yml`. That workflow runs `uv build`, uploads the wheel as a workflow artifact, and publishes it via `pypa/gh-action-pypi-publish` using PyPI trusted publishing (OIDC, so no API token sits in repo secrets).

### Important sequencing detail

PyPI publication is gated on the **GitHub release**, not the **tag**. If the script's `gh release create` step fails after the tag has been pushed, the tag exists but the publish workflow never fires. Recovery: fix the underlying issue, then rerun `gh release create v<version>` manually. The publish workflow will pick it up.

To redo a release before publishing has succeeded:

```bash
git tag -d v<version>
git push origin :refs/tags/v<version>
# fix the issue, then rerun ./scripts/release.sh <version>
```

## CHANGELOG conventions

`release.sh` parses `CHANGELOG.md`, so the format matters.

* Style is [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Sections used in this project: `Added`, `Changed`, `Fixed`, `Breaking`, `Performance`, `Repo polish`, `Notes`.
* Every change lands under `## [Unreleased]` first.
* Cutting a release means:
  1. Renaming `## [Unreleased]` (or copying its entries) under a new `## [<version>] — <YYYY-MM-DD>` heading.
  2. Adding a corresponding reference link at the bottom: `[<version>]: https://github.com/luiul/dbts/compare/v<prev>...v<version>`.
  3. Re-creating an empty `## [Unreleased]` section above it.
* Versioning follows [SemVer](https://semver.org/spec/v2.0.0.html).

## Versioning policy

The project is pre-1.0. While on `0.x`:

* `0.X.0` for new features or behavior changes (e.g. `0.7.0` added `dbts publish`).
* `0.x.Y` for fix-only releases (e.g. `0.8.2` silenced a noisy boto3 traceback).

## CI reference

| Workflow | Trigger | What it does |
| --- | --- | --- |
| `ci.yml` | push to `main`, any PR | `uv lock --check` → `uv sync --group dev` → `ruff format --check` → `ruff check` → `ty check` → `pytest`, on Python 3.11 / 3.12 / 3.13. |
| `publish.yml` | GitHub release published | `uv build` → upload artifact → publish to PyPI via OIDC trusted publishing. |

## Where to look next

* User-facing usage and command reference: [`README.md`](../README.md).
* Release script details and recovery tips: [`scripts/README.md`](../scripts/README.md).
* History: [`CHANGELOG.md`](../CHANGELOG.md).
* Open work and bug reports: [GitHub Issues](https://github.com/luiul/dbts/issues).
