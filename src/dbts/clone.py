from __future__ import annotations

import getpass
import re
from datetime import datetime, timezone
from typing import Literal

from rich.console import Console
from rich.table import Table

from dbts.config import (
    ConfigError,
    Target,
    default_profile_name,
    read_profile,
    sandbox_user,
)
from dbts.snowflake import connect, run_sql

Source = Literal["staging", "live"]
SOURCES: tuple[Source, ...] = ("staging", "live")

COMMENT_PATTERN = re.compile(
    r"^dbts: cloned from (?P<source>\w+) at (?P<ts>[\d:T+\-Z\.]+) by (?P<user>\S+)"
)

console = Console()


def _sandbox_target() -> Target:
    return read_profile(default_profile_name(), "sandbox")


def _source_target(source: Source) -> Target:
    return read_profile(default_profile_name(), source)


def _sql_quote(s: str) -> str:
    return s.replace("'", "''")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _show_database(conn, db: str) -> dict | None:
    rows = run_sql(conn, f"SHOW DATABASES LIKE '{_sql_quote(db)}'")
    return rows[0] if rows else None


def _confirm(prompt: str) -> bool:
    answer = console.input(f"[yellow]{prompt}[/yellow] [bold]\\[y/N][/bold] ").strip().lower()
    return answer in {"y", "yes"}


def up(source: Source) -> int:
    sandbox = _sandbox_target()
    src = _source_target(source)
    user = sandbox_user(sandbox)
    target_db = sandbox.database.upper()
    source_db = src.database.upper()

    conn = connect(sandbox)
    try:
        existing = _show_database(conn, target_db)
        if existing:
            comment = (existing.get("comment") or "").strip()
            match = COMMENT_PATTERN.match(comment)
            if match and match.group("source") == source:
                console.print(
                    f"[green]Sandbox already exists[/green]: {target_db} "
                    f"(cloned from {source} at {match.group('ts')})"
                )
                return 0
            current_source = match.group("source") if match else "?"
            console.print(
                f"[red]Sandbox exists but was cloned from '{current_source}', "
                f"not '{source}'.[/red] Run [bold]dbts refresh --from {source}[/bold] "
                f"to re-clone."
            )
            return 1

        comment = (
            f"dbts: cloned from {source} at {_now_iso()} "
            f"by {getpass.getuser()}"
        )
        ddl = (
            f"CREATE DATABASE {target_db} CLONE {source_db} "
            f"COMMENT = '{_sql_quote(comment)}'"
        )
        console.print(f"[dim]{ddl}[/dim]")
        run_sql(conn, ddl)
        console.print(
            f"[green]Created sandbox[/green] {target_db} "
            f"(zero-copy clone of {source_db}, user={user})"
        )
        return 0
    finally:
        conn.close()


def refresh(source: Source) -> int:
    sandbox = _sandbox_target()
    src = _source_target(source)
    target_db = sandbox.database.upper()
    source_db = src.database.upper()

    if not _confirm(
        f"This will DROP and re-create {target_db} from {source_db}. Continue?"
    ):
        console.print("[yellow]Aborted.[/yellow]")
        return 1

    conn = connect(sandbox)
    try:
        comment = (
            f"dbts: cloned from {source} at {_now_iso()} "
            f"by {getpass.getuser()}"
        )
        ddl = (
            f"CREATE OR REPLACE DATABASE {target_db} CLONE {source_db} "
            f"COMMENT = '{_sql_quote(comment)}'"
        )
        console.print(f"[dim]{ddl}[/dim]")
        run_sql(conn, ddl)
        console.print(
            f"[green]Refreshed sandbox[/green] {target_db} from {source_db}"
        )
        return 0
    finally:
        conn.close()


def drop() -> int:
    sandbox = _sandbox_target()
    target_db = sandbox.database.upper()

    if not _confirm(f"This will DROP DATABASE {target_db}. Continue?"):
        console.print("[yellow]Aborted.[/yellow]")
        return 1

    conn = connect(sandbox)
    try:
        ddl = f"DROP DATABASE IF EXISTS {target_db}"
        console.print(f"[dim]{ddl}[/dim]")
        run_sql(conn, ddl)
        console.print(f"[green]Dropped sandbox[/green] {target_db}")
        return 0
    finally:
        conn.close()


def status() -> int:
    sandbox = _sandbox_target()
    target_db = sandbox.database.upper()

    conn = connect(sandbox)
    try:
        existing = _show_database(conn, target_db)
        if not existing:
            console.print(
                f"[yellow]Sandbox not found:[/yellow] {target_db}\n"
                f"Run [bold]dbts up --from staging|live[/bold] to create it."
            )
            return 1

        comment = (existing.get("comment") or "").strip()
        match = COMMENT_PATTERN.match(comment)
        source = match.group("source") if match else "?"
        cloned_at = match.group("ts") if match else "?"
        cloned_by = match.group("user") if match else "?"

        table = Table(show_header=False, box=None, pad_edge=False)
        table.add_column(style="bold")
        table.add_column()
        table.add_row("database", target_db)
        table.add_row("created_on", str(existing.get("created_on", "?")))
        table.add_row("origin", existing.get("origin") or "?")
        table.add_row("source", source)
        table.add_row("cloned_at", cloned_at)
        table.add_row("cloned_by", cloned_by)
        console.print(table)
        return 0
    finally:
        conn.close()


def exists() -> bool:
    """Return True iff the sandbox database exists."""
    sandbox = _sandbox_target()
    target_db = sandbox.database.upper()
    conn = connect(sandbox)
    try:
        return _show_database(conn, target_db) is not None
    finally:
        conn.close()


def require_exists() -> None:
    """Raise a friendly error if the sandbox isn't provisioned."""
    if exists():
        return
    sandbox = _sandbox_target()
    target_db = sandbox.database.upper()
    raise ConfigError(
        f"sandbox database {target_db} does not exist. "
        f"Run `dbts up --from staging|live` first."
    )
