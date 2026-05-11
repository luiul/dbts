from __future__ import annotations

import sys
from enum import StrEnum

import typer
from rich.console import Console

from dbts import clone, dbt_runner, log, plan
from dbts.config import (
    ConfigError,
    default_profile_name,
    read_profile,
)

HELP_OPTION_NAMES = ["-h", "--help"]

PANEL_LIFECYCLE = "Sandbox lifecycle"
PANEL_DBT = "dbt pass-through"
PANEL_META = "Meta"

APP_HELP = """\
dbt environment runner with Snowflake zero-copy clone sandboxes.

`dbts` manages a per-developer zero-copy clone of staging or live, then runs dbt
against it. Lifecycle commands (up/refresh/drop/status) manage the sandbox; all
other commands pass through to dbt with `--target sandbox` by default.

Examples:
  dbts up --from staging              # create the sandbox clone
  dbts plan my_model+ --target live   # preview the build set without running it
  dbts build my_model+                # dbt build against sandbox (selectors work bare)
  dbts test +my_model+ --target live  # dbt test against the live target
  dbts status                         # show the sandbox's source, age, owner
  dbts refresh --from staging         # drop and re-clone
  dbts drop                           # drop the sandbox

Profile resolution: $DBTS_PROFILE first, then `profile:` in dbt_project.yml.
"""

app = typer.Typer(
    help=APP_HELP,
    no_args_is_help=True,
    add_completion=False,
    context_settings={"help_option_names": HELP_OPTION_NAMES},
    rich_markup_mode="rich",
)

err = Console(stderr=True)


@app.callback()
def _root(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable debug logging."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress info logging."),
) -> None:
    log.configure(verbose=verbose, quiet=quiet)


class SourceEnum(StrEnum):
    staging = "staging"
    live = "live"


class TargetEnum(StrEnum):
    sandbox = "sandbox"
    staging = "staging"
    live = "live"
    dev = "dev"


# --------------------------------------------------------------------------- #
# Lifecycle commands (sandbox-only)                                           #
# --------------------------------------------------------------------------- #


@app.command("up", rich_help_panel=PANEL_LIFECYCLE)
def cmd_up(
    from_: SourceEnum = typer.Option(..., "--from", help="Database to clone from."),
) -> None:
    """Create the sandbox as a zero-copy clone of staging or live."""
    _run_or_exit(lambda: clone.up(from_.value))


@app.command("refresh", rich_help_panel=PANEL_LIFECYCLE)
def cmd_refresh(
    from_: SourceEnum = typer.Option(..., "--from", help="Database to re-clone from."),
) -> None:
    """Drop and re-create the sandbox from staging or live."""
    _run_or_exit(lambda: clone.refresh(from_.value))


@app.command("drop", rich_help_panel=PANEL_LIFECYCLE)
def cmd_drop() -> None:
    """Drop the sandbox database."""
    _run_or_exit(clone.drop)


@app.command("status", rich_help_panel=PANEL_LIFECYCLE)
def cmd_status() -> None:
    """Show the sandbox's source, age, and owner."""
    _run_or_exit(clone.status)


# --------------------------------------------------------------------------- #
# dbt pass-through commands                                                   #
# --------------------------------------------------------------------------- #

DBT_SUBCOMMANDS: tuple[str, ...] = (
    "run",
    "build",
    "test",
    "compile",
    "debug",
    "seed",
    "snapshot",
    "ls",
    "deps",
    "source",
    "docs",
    "parse",
    "show",
    "clean",
)

# Subcommands where bare positional args (e.g. `my_model+`) should be promoted
# to `--select <args>` so users can type `dbts build my_model+` like dbt 1.x docs imply.
DBT_SELECTOR_SUBCOMMANDS: frozenset[str] = frozenset(
    {"run", "build", "test", "compile", "seed", "snapshot", "ls", "show"}
)


_DBT_FLAGS_WITH_VALUE: frozenset[str] = frozenset(
    {
        "--target",
        "-t",
        "--select",
        "-s",
        "--vars",
        "--profile",
        "--profiles-dir",
        "--project-dir",
        "--exclude",
        "--selector",
        "--state",
        "--threads",
        "--defer-state",
        "--favor-state",
        "--macro",
        "--resource-type",
        "--output",
        "--output-keys",
    }
)


def _promote_selectors(subcommand: str, args: list[str]) -> list[str]:
    """Wrap bare positional args in `--select` for selector-aware dbt subcommands.

    dbt expects `--select`/`-s` for graph selectors; passing them bare yields
    "Got unexpected extra argument". This pulls bare positionals out and
    appends them as an additional `--select <args>`. dbt unions multiple
    `--select` flags, so combining them with an explicit `--select`/`-s` works
    naturally.
    """
    if subcommand not in DBT_SELECTOR_SUBCOMMANDS:
        return args

    positionals: list[str] = []
    rest: list[str] = []
    i = 0
    while i < len(args):
        tok = args[i]
        if tok.startswith("-"):
            rest.append(tok)
            if "=" not in tok and tok in _DBT_FLAGS_WITH_VALUE and i + 1 < len(args):
                rest.append(args[i + 1])
                i += 2
                continue
            i += 1
            continue
        positionals.append(tok)
        i += 1

    if not positionals:
        return args
    return [*rest, "--select", " ".join(positionals)]


DBT_CONTEXT_SETTINGS: dict[str, object] = {
    "allow_extra_args": True,
    "ignore_unknown_options": True,
    "help_option_names": HELP_OPTION_NAMES,
}


def _make_dbt_passthrough(subcommand: str):
    def _cmd(
        ctx: typer.Context,
        target: TargetEnum = typer.Option(
            TargetEnum.sandbox,
            "--target",
            "-t",
            help="dbt target to run against. Default: sandbox.",
        ),
    ) -> None:
        try:
            if target == TargetEnum.sandbox:
                clone.require_exists()
            target_cfg = read_profile(default_profile_name(), target.value)
            forwarded = _promote_selectors(subcommand, list(ctx.args))
            rc = dbt_runner.run(subcommand, forwarded, target.value, target_cfg)
        except ConfigError as e:
            _print_config_error(e)
            raise typer.Exit(code=1) from e
        raise typer.Exit(code=rc)

    _cmd.__name__ = f"dbt_{subcommand}"
    if subcommand in DBT_SELECTOR_SUBCOMMANDS:
        _cmd.__doc__ = (
            f"Pass-through to `dbt {subcommand}`. Bare positional args are forwarded "
            "as `--select <args>` (e.g. `dbts build my_model+`)."
        )
    else:
        _cmd.__doc__ = f"Pass-through to `dbt {subcommand}`."
    return _cmd


for _sub in DBT_SUBCOMMANDS:
    app.command(_sub, context_settings=DBT_CONTEXT_SETTINGS, rich_help_panel=PANEL_DBT)(_make_dbt_passthrough(_sub))


@app.command(
    "plan",
    context_settings=DBT_CONTEXT_SETTINGS,
    rich_help_panel=PANEL_LIFECYCLE,
)
def cmd_plan(
    ctx: typer.Context,
    target: TargetEnum = typer.Option(
        TargetEnum.sandbox,
        "--target",
        "-t",
        help="dbt target whose env/profile is used to parse the project. Default: sandbox.",
    ),
) -> None:
    """Preview the build set for given selectors. Offline; no Snowflake connection."""
    try:
        target_cfg = read_profile(default_profile_name(), target.value)
        forwarded = _promote_selectors("ls", list(ctx.args))
        rc = plan.run(forwarded, target.value, target_cfg)
    except ConfigError as e:
        _print_config_error(e)
        raise typer.Exit(code=1) from e
    raise typer.Exit(code=rc)


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _print_config_error(e: ConfigError) -> None:
    msg = str(e)
    err.print(f"[red]error:[/red] {msg}")
    hint = _hint_for(msg)
    if hint:
        err.print(f"[yellow]hint:[/yellow] {hint}")


def _hint_for(msg: str) -> str | None:
    lowered = msg.lower()
    if "could not determine dbt profile name" in lowered:
        return "set $DBTS_PROFILE or add `profile: <name>` to dbt_project.yml."
    if "could not find dbt_project.yml" in lowered:
        return "run dbts from inside a dbt project directory."
    if "sandbox database" in lowered and "does not exist" in lowered:
        return "create it with `dbts up --from staging` or `--from live`."
    if "does not match the expected pattern" in lowered:
        return "the sandbox target's `database:` must look like <PREFIX>_SANDBOX_<USER>."
    if "profile '" in lowered and "not found in" in lowered:
        return "check the profile name in dbt_project.yml or set $DBTS_PROFILE."
    if "target '" in lowered and "not found under profile" in lowered:
        return "add the missing target (e.g. `sandbox:`) to the profile in ~/.dbt/profiles.yml."
    if "dbt not found on path" in lowered:
        return "activate the venv where dbt-core is installed, or `pip install dbt-snowflake`."
    return None


def _run_or_exit(fn) -> None:
    try:
        rc = fn()
    except ConfigError as e:
        _print_config_error(e)
        raise typer.Exit(code=1) from e
    raise typer.Exit(code=rc or 0)


@app.command("version", rich_help_panel=PANEL_META)
def cmd_version() -> None:
    """Print the installed dbts version."""
    from dbts import __version__

    typer.echo(__version__)


def main() -> None:  # pragma: no cover
    app()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(app())
