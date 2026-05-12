from __future__ import annotations

import sys
from enum import StrEnum

import typer
from rich.console import Console

from dbts import clone, dbt_runner, freshness, log, plan
from dbts.config import (
    ConfigError,
    default_profile_name,
    read_profile,
)

HELP_OPTION_NAMES = ["-h", "--help"]

PANEL_SANDBOX = "Sandbox"
PANEL_INSPECT = "Inspect"
PANEL_DBT = "dbt pass-through"
PANEL_META = "Meta"

APP_HELP = """\
dbt environment runner with Snowflake zero-copy clone sandboxes.

`dbts` manages a per-developer zero-copy clone of staging or live, then runs dbt
against it. dbt pass-through commands accept bare positional model selectors
(e.g. `dbts build my_model+`) and default to `--target sandbox`.

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


@app.command("up", rich_help_panel=PANEL_SANDBOX)
def cmd_up(
    from_: SourceEnum = typer.Option(..., "--from", help="Database to clone from."),
) -> None:
    """Create the sandbox as a zero-copy clone of staging or live."""
    _run_or_exit(lambda: clone.up(from_.value))


@app.command("refresh", rich_help_panel=PANEL_SANDBOX)
def cmd_refresh(
    from_: SourceEnum = typer.Option(..., "--from", help="Database to re-clone from."),
) -> None:
    """Drop and re-create the sandbox from staging or live."""
    _run_or_exit(lambda: clone.refresh(from_.value))


@app.command("drop", rich_help_panel=PANEL_SANDBOX)
def cmd_drop() -> None:
    """Drop the sandbox database."""
    _run_or_exit(clone.drop)


@app.command("status", rich_help_panel=PANEL_SANDBOX)
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


_SELECTOR_FLAGS: frozenset[str] = frozenset({"--select", "-s", "--exclude"})


def _promote_selectors(subcommand: str, args: list[str]) -> list[str]:
    """Attach bare positional args to the most recent selector flag.

    dbt rejects bare positional model names; they must be passed via
    `--select`/`-s`/`--exclude`. We walk the args left-to-right and attribute
    each bare positional to whichever selector flag last appeared (defaulting
    to `--select` if none has). This makes invocations like
    ``dbts build foo --exclude bar baz`` do what the user almost always means:
    ``--select foo --exclude bar --exclude baz`` — instead of silently
    promoting ``baz`` back to ``--select``.

    dbt accepts repeated `--select` / `--exclude` flags and unions their
    values, so the chained-flag output is semantically equivalent to a single
    space-separated value but easier to reason about.
    """
    if subcommand not in DBT_SELECTOR_SUBCOMMANDS:
        return args

    out: list[str] = []
    current_mode = "--select"
    i = 0
    while i < len(args):
        tok = args[i]
        if tok.startswith("-"):
            if "=" not in tok and tok in _DBT_FLAGS_WITH_VALUE and i + 1 < len(args):
                out.append(tok)
                out.append(args[i + 1])
                if tok in _SELECTOR_FLAGS:
                    current_mode = tok
                i += 2
                continue
            out.append(tok)
            i += 1
            continue
        out.append(current_mode)
        out.append(tok)
        i += 1
    return out


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
    _cmd.__doc__ = f"Pass-through to `dbt {subcommand}`."
    return _cmd


for _sub in DBT_SUBCOMMANDS:
    app.command(_sub, context_settings=DBT_CONTEXT_SETTINGS, rich_help_panel=PANEL_DBT)(_make_dbt_passthrough(_sub))


@app.command(
    "plan",
    context_settings=DBT_CONTEXT_SETTINGS,
    rich_help_panel=PANEL_INSPECT,
)
def cmd_plan(
    ctx: typer.Context,
    target: TargetEnum = typer.Option(
        TargetEnum.sandbox,
        "--target",
        "-t",
        help="dbt target whose env/profile is used to parse the project. Default: sandbox.",
    ),
    cost: bool = typer.Option(
        False,
        "--cost",
        help="Estimate Snowflake credits + runtime from QUERY_HISTORY. Off by default (offline).",
    ),
    days: int = typer.Option(
        7,
        "--days",
        min=1,
        max=365,
        help="Lookback window for QUERY_HISTORY when --cost is on. Default: 7.",
    ),
) -> None:
    """Preview the build set for given selectors. Optionally estimate Snowflake cost."""
    try:
        target_cfg = read_profile(default_profile_name(), target.value)
        forwarded = _promote_selectors("ls", list(ctx.args))
        rc = plan.run(forwarded, target.value, target_cfg, with_cost=cost, days=days)
    except ConfigError as e:
        _print_config_error(e)
        raise typer.Exit(code=1) from e
    raise typer.Exit(code=rc)


@app.command(
    "freshness",
    context_settings=DBT_CONTEXT_SETTINGS,
    rich_help_panel=PANEL_INSPECT,
)
def cmd_freshness(
    ctx: typer.Context,
    target: TargetEnum = typer.Option(
        TargetEnum.live,
        "--target",
        "-t",
        help=("dbt target to audit. Defaults to live since post-incident freshness checks usually target production."),
    ),
    since: str | None = typer.Option(
        None,
        "--since",
        help=(
            "Stale threshold. Accepts ISO datetime (`2026-05-09T17:00:00Z`), "
            "ISO date (`2026-05-09`), or relative (`24h`, `7d`, `1w`). "
            "Default: 6 hours before the freshest table in the set."
        ),
    ),
) -> None:
    """Audit lineage freshness — flag tables not touched recently."""
    try:
        target_cfg = read_profile(default_profile_name(), target.value)
        forwarded = _promote_selectors("ls", list(ctx.args))
        rc = freshness.run(forwarded, target.value, target_cfg, since=since)
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
        return "run `dbts up --from staging` (dev work) or `--from live` (prod data). Zero-copy, instant."
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
