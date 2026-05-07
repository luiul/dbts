from __future__ import annotations

import sys
from enum import Enum

import typer
from rich.console import Console

from dbts import clone, dbt_runner
from dbts.config import (
    ConfigError,
    default_profile_name,
    read_profile,
)

app = typer.Typer(
    help=(
        "dbt environment runner with Snowflake zero-copy clone sandboxes. "
        "Default --target is 'sandbox'; lifecycle commands (up/refresh/drop/status) "
        "manage the sandbox database."
    ),
    no_args_is_help=True,
    add_completion=False,
)

err = Console(stderr=True)


class SourceEnum(str, Enum):
    staging = "staging"
    live = "live"


class TargetEnum(str, Enum):
    sandbox = "sandbox"
    staging = "staging"
    live = "live"
    dev = "dev"


# --------------------------------------------------------------------------- #
# Lifecycle commands (sandbox-only)                                           #
# --------------------------------------------------------------------------- #


@app.command("up")
def cmd_up(
    from_: SourceEnum = typer.Option(..., "--from", help="Database to clone from."),
) -> None:
    """Create the sandbox database as a zero-copy clone of staging or live."""
    _run_or_exit(lambda: clone.up(from_.value))


@app.command("refresh")
def cmd_refresh(
    from_: SourceEnum = typer.Option(..., "--from", help="Database to re-clone from."),
) -> None:
    """Drop and re-create the sandbox database from staging or live."""
    _run_or_exit(lambda: clone.refresh(from_.value))


@app.command("drop")
def cmd_drop() -> None:
    """Drop the sandbox database."""
    _run_or_exit(clone.drop)


@app.command("status")
def cmd_status() -> None:
    """Show details about the sandbox database (source, age, owner)."""
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

DBT_PASSTHROUGH = {
    "context_settings": {
        "allow_extra_args": True,
        "ignore_unknown_options": True,
    }
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
        except ConfigError as e:
            err.print(f"[red]error:[/red] {e}")
            raise typer.Exit(code=1)

        rc = dbt_runner.run(subcommand, ctx.args, target.value, target_cfg)
        raise typer.Exit(code=rc)

    _cmd.__name__ = f"dbt_{subcommand}"
    _cmd.__doc__ = f"Run `dbt {subcommand}` against the chosen target."
    return _cmd


for _sub in DBT_SUBCOMMANDS:
    app.command(_sub, **DBT_PASSTHROUGH)(_make_dbt_passthrough(_sub))


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _run_or_exit(fn) -> None:
    try:
        rc = fn()
    except ConfigError as e:
        err.print(f"[red]error:[/red] {e}")
        raise typer.Exit(code=1)
    raise typer.Exit(code=rc or 0)


@app.command("version")
def cmd_version() -> None:
    """Print the installed dbts version."""
    from importlib.metadata import version as pkg_version

    typer.echo(pkg_version("dbts"))


def main() -> None:  # pragma: no cover
    app()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(app())
