"""Lineage freshness audit — answers 'did the chain run end-to-end?'.

Reads `INFORMATION_SCHEMA.TABLES.LAST_ALTERED` for each table in the build
set, sorts topologically by dbt's `depends_on.nodes`, and highlights stale
links in red.
"""

from __future__ import annotations

import contextlib
import logging
import re
import subprocess
import threading
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from rich.console import Console
from rich.table import Table

from dbts import dbt_runner
from dbts._dbt_ls import (
    DEFAULT_OUTPUT_KEYS,
    has_flag,
    parse_json_lines,
    strip_ls_incompatible,
)
from dbts.config import Target
from dbts.cost import format_relative
from dbts.snowflake import connect, run_sql

DEFAULT_STALE_WINDOW = timedelta(hours=6)

log = logging.getLogger("dbts.freshness")
console = Console()


@dataclass(frozen=True)
class TableInfo:
    database: str
    schema: str
    name: str
    last_altered: datetime | None
    row_count: int | None


def run(
    args: Iterable[str],
    target_name: str,
    target: Target,
    since: str | None = None,
) -> int:
    """Audit `LAST_ALTERED` for every table in the selected lineage."""
    dbt_runner.ensure_dbt_on_path()

    arg_list = strip_ls_incompatible(list(args))
    cmd = [
        "dbt",
        "ls",
        "--target",
        target_name,
        "--output",
        "json",
        "--output-keys",
        DEFAULT_OUTPUT_KEYS,
        *arg_list,
    ]
    if not has_flag(arg_list, "--resource-type"):
        cmd.extend(["--resource-type", "model"])

    # Auth in parallel with `dbt ls` — same trick as plan.py.
    conn_holder: dict[str, Any] = {}
    log.info("[dim]Connecting to Snowflake...[/dim]")
    conn_thread = threading.Thread(target=_open_connection, args=(target, conn_holder), daemon=True)
    conn_thread.start()

    completed = subprocess.run(
        cmd,
        cwd=str(dbt_runner.project_root()),
        env=dbt_runner.dbt_env(target_name, target),
        capture_output=True,
        text=True,
        check=False,
    )

    if completed.returncode != 0:
        conn_thread.join()
        _close(conn_holder.get("conn"))
        if completed.stderr:
            console.print(completed.stderr.rstrip(), style="red")
        if completed.stdout:
            console.print(completed.stdout.rstrip())
        return completed.returncode

    records = parse_json_lines(completed.stdout)
    if not records:
        conn_thread.join()
        _close(conn_holder.get("conn"))
        log.warning("[yellow]Build set is empty[/yellow] — your selectors matched nothing.")
        return 0

    conn_thread.join()
    if "error" in conn_holder:
        log.error(
            "[red]Cannot check freshness:[/red] %s",
            _short_error(conn_holder["error"]),
        )
        return 1
    conn = conn_holder.get("conn")
    if conn is None:
        log.error("[red]Cannot check freshness: no Snowflake connection.[/red]")
        return 1

    sandbox_user = _resolve_sandbox_user(target_name, target)

    try:
        infos = fetch_table_metadata(conn, records, target_name, sandbox_user)
    except Exception as e:  # snowflake.connector raises a zoo of types
        log.error(
            "[red]Cannot read INFORMATION_SCHEMA:[/red] %s",
            _short_error(e),
        )
        return 1
    finally:
        _close(conn)

    info_by_table = {(i.database, i.schema, i.name): i for i in infos}
    sorted_records = topological_sort(records)
    threshold, baseline_ts = _compute_threshold(infos, since)
    _render(
        sorted_records,
        info_by_table,
        threshold,
        baseline_ts,
        since,
        target_name,
        sandbox_user,
    )
    return 0


def _resolve_sandbox_user(target_name: str, target: Target) -> str | None:
    """Extract `<USER>` segment from a sandbox target's database name.

    Only applies when target_name == 'sandbox'. Mirrors `dbts.config.sandbox_user`
    but tolerant: returns None instead of raising if the pattern doesn't match.
    """
    if target_name.lower() != "sandbox":
        return None
    try:
        from dbts.config import sandbox_user as _sandbox_user_strict

        return _sandbox_user_strict(target)
    except Exception:  # ConfigError when the pattern doesn't match
        return None


def _open_connection(target: Target, holder: dict[str, Any]) -> None:
    try:
        holder["conn"] = connect(target)
    except Exception as e:  # surfaced to caller via holder["error"]
        holder["error"] = e


def _close(conn: Any) -> None:
    if conn is None:
        return
    with contextlib.suppress(Exception):
        conn.close()


def _short_error(e: Exception) -> str:
    msg = str(e).strip()
    first_line = msg.splitlines()[0] if msg else type(e).__name__
    return first_line[:200]


# ----- record helpers -------------------------------------------------------- #


def _physical(record: dict, target_name: str, sandbox_user: str | None) -> tuple[str, str, str]:
    """Return the (database, schema, name) triplet, uppercased.

    `dbt ls` reports `config.database` as the static value from
    `dbt_project.yml`. The actual warehouse database for non-`live`/non-`dev`
    targets is computed at run time by the project's `generate_database_name`
    macro, which appends an env postfix:

        sandbox  -> <DB>_SANDBOX_<USER>
        staging  -> <DB>_STAGING
        dev      -> <DB>_DEV

    Mirroring that here so `--target sandbox` actually audits the sandbox
    clone, not whatever `config.database` happens to point at.
    """
    cfg = record.get("config") or {}
    database = str(cfg.get("database") or "").upper()
    schema = str(cfg.get("schema") or "").upper()
    name = str(cfg.get("alias") or record.get("name") or "").upper()

    postfix = _env_postfix(target_name, sandbox_user)
    if database and postfix:
        # Convention: macros append before _SENSITIVE if present.
        if database.endswith("_SENSITIVE"):
            database = database[: -len("_SENSITIVE")] + postfix + "_SENSITIVE"
        else:
            database = database + postfix

    return database, schema, name


def _env_postfix(target_name: str, sandbox_user: str | None) -> str:
    """Return the database-name postfix for the given target, or empty string."""
    target_lower = target_name.lower()
    if target_lower == "sandbox":
        if not sandbox_user:
            return ""
        return f"_SANDBOX_{sandbox_user.upper()}"
    if target_lower == "staging":
        return "_STAGING"
    if target_lower == "dev":
        return "_DEV"
    return ""


# ----- Snowflake ------------------------------------------------------------- #


def fetch_table_metadata(
    conn: Any,
    records: list[dict],
    target_name: str,
    sandbox_user: str | None,
) -> list[TableInfo]:
    """One `INFORMATION_SCHEMA.TABLES` query per database covering all records."""
    by_db: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for r in records:
        db, schema, name = _physical(r, target_name, sandbox_user)
        if not db or not schema or not name:
            continue
        by_db[db].add((schema, name))

    out: list[TableInfo] = []
    for db, pairs in by_db.items():
        if not pairs:
            continue
        schemas = ",".join("'" + s.replace("'", "''") + "'" for s, _ in pairs)
        names = ",".join("'" + n.replace("'", "''") + "'" for _, n in pairs)
        sql = f"""
            SELECT
                table_catalog AS database,
                table_schema  AS schema,
                table_name    AS name,
                last_altered,
                row_count
            FROM "{db}".INFORMATION_SCHEMA.TABLES
            WHERE table_schema IN ({schemas})
              AND table_name IN ({names})
        """
        rows = run_sql(conn, sql)
        for row in rows:
            out.append(
                TableInfo(
                    database=row["DATABASE"],
                    schema=row["SCHEMA"],
                    name=row["NAME"],
                    last_altered=row.get("LAST_ALTERED"),
                    row_count=(int(row["ROW_COUNT"]) if row.get("ROW_COUNT") is not None else None),
                )
            )
    return out


# ----- topo sort ------------------------------------------------------------- #


def topological_sort(records: list[dict]) -> list[dict]:
    """Kahn's algorithm, keyed by model name within the build set.

    Parents within the build set come before children; alphabetical tiebreak
    for stability. Two records sharing a name (different projects) is not a
    real-world scenario — a single dbt build set has unique names.
    """
    by_name: dict[str, dict] = {r["name"]: r for r in records if r.get("name")}

    in_degree: dict[str, int] = dict.fromkeys(by_name, 0)
    children: dict[str, list[str]] = defaultdict(list)
    for name, r in by_name.items():
        for parent_uid in (r.get("depends_on") or {}).get("nodes") or []:
            parent_name = _name_from_unique_id(parent_uid)
            if parent_name and parent_name in by_name and parent_name != name:
                children[parent_name].append(name)
                in_degree[name] += 1

    ready = sorted(n for n, deg in in_degree.items() if deg == 0)
    out: list[dict] = []
    while ready:
        next_ready: list[str] = []
        for n in ready:
            out.append(by_name[n])
            for child in children.get(n, []):
                in_degree[child] -= 1
                if in_degree[child] == 0:
                    next_ready.append(child)
        ready = sorted(next_ready)

    # Cycles (shouldn't happen in dbt) get appended in name order so we don't
    # drop them silently.
    if len(out) < len(by_name):
        seen = {id(r) for r in out}
        for n in sorted(by_name):
            if id(by_name[n]) not in seen:
                out.append(by_name[n])
    return out


def _name_from_unique_id(unique_id: str) -> str | None:
    """Extract the model name from `model.<project>.<name>` references."""
    m = re.match(r"^model\.[^.]+\.(.+)$", unique_id)
    return m.group(1) if m else None


# ----- threshold ------------------------------------------------------------- #


_RELATIVE_RE = re.compile(r"^\s*(\d+)\s*([smhdw])\s*$", re.IGNORECASE)


def _compute_threshold(
    infos: list[TableInfo],
    since: str | None,
) -> tuple[datetime | None, datetime | None]:
    """Return (threshold, baseline). Tables older than threshold are stale.

    - `since` parses as ISO datetime, ISO date, or relative ('24h', '7d', '1w').
    - If absent, threshold = max(last_altered) - 6h. Baseline = max(last_altered).
    """
    timestamps = [i.last_altered for i in infos if i.last_altered]
    baseline = max(timestamps) if timestamps else None

    if since:
        return parse_since(since), baseline
    if baseline is None:
        return None, None
    return baseline - DEFAULT_STALE_WINDOW, baseline


def parse_since(value: str) -> datetime:
    """Parse `--since` arg. Supports relative ('24h') and absolute (ISO)."""
    rel = _RELATIVE_RE.match(value)
    if rel:
        amount = int(rel.group(1))
        unit = rel.group(2).lower()
        seconds = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}[unit]
        return datetime.now(tz=UTC) - timedelta(seconds=amount * seconds)
    # Try ISO datetime, then ISO date.
    s = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError as e:
        raise ValueError(f"unparseable --since value: {value!r}") from e
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


# ----- render ---------------------------------------------------------------- #


def _render(
    records: list[dict],
    info_by_table: dict[tuple[str, str, str], TableInfo],
    threshold: datetime | None,
    baseline_ts: datetime | None,
    since_str: str | None,
    target_name: str,
    sandbox_user: str | None,
) -> None:
    now = datetime.now(tz=UTC)
    stale_records: list[dict] = []

    table = Table(
        show_header=True,
        header_style="dim",
        box=None,
        pad_edge=False,
        padding=(0, 2),
    )
    table.add_column("model", overflow="fold")
    table.add_column("database.schema", overflow="fold")
    table.add_column("last altered")
    table.add_column("ago", justify="right")
    table.add_column("rows", justify="right")

    for r in records:
        name = r.get("name") or "?"
        triplet = _physical(r, target_name, sandbox_user)
        info = info_by_table.get(triplet)

        is_stale = False
        if info and info.last_altered:
            ts_str = info.last_altered.strftime("%Y-%m-%d %H:%M UTC")
            ago_str = format_relative(info.last_altered, now)
            if threshold is not None and info.last_altered < threshold:
                is_stale = True
        else:
            ts_str = "(missing)"
            ago_str = "—"
            is_stale = True

        rows_str = f"{info.row_count:,}" if info and info.row_count is not None else "—"

        if is_stale:
            stale_records.append(r)
            table.add_row(
                f"[red]{name}[/red]",
                f"[red]{triplet[0]}.{triplet[1]}[/red]",
                f"[red]{ts_str}[/red]",
                f"[red]{ago_str}[/red]",
                f"[red]{rows_str}[/red]",
            )
        else:
            table.add_row(name, f"{triplet[0]}.{triplet[1]}", ts_str, ago_str, rows_str)

    header_bits = [f"[bold]Lineage freshness check[/bold] ({len(records)} models"]
    if baseline_ts is not None:
        header_bits.append(f"baseline {baseline_ts.strftime('%Y-%m-%d %H:%M UTC')}")
    if threshold is not None:
        if since_str is None:
            header_bits.append("threshold = baseline - 6h")
        else:
            header_bits.append(f"--since {since_str}")
    console.print(", ".join(header_bits) + ")")
    console.print()
    console.print(table)
    console.print()

    fresh_count = len(records) - len(stale_records)
    if not stale_records:
        console.print(f"[green]All {len(records)} models fresh.[/green]")
        return

    console.print(
        f"[bold red]Stale:[/bold red] {len(stale_records)}/{len(records)} models. "
        f"[bold green]Fresh:[/bold green] {fresh_count}/{len(records)}."
    )
    console.print()
    console.print("[bold]Stale tables:[/bold]")
    for r in stale_records:
        console.print(f"  {r.get('name')}")

    suggestion = _suggest_rebuild(stale_records, records)
    if suggestion:
        console.print()
        console.print("[bold]Suggested build (covers all stale tables):[/bold]")
        console.print(f"  [cyan]{suggestion}[/cyan]")


# ----- suggested rebuild ----------------------------------------------------- #


def _suggest_rebuild(stale_records: list[dict], all_records: list[dict]) -> str | None:
    """Return a `dbts build --select X+ Y+ ...` line covering all stale models.

    Picks the minimum set of stale-roots: a stale model whose parents (within
    the build set) are NOT stale. Building each such root with `+` propagates
    downstream and catches up the chain without rebuilding tables already
    fresh upstream.
    """
    if not stale_records:
        return None

    in_set: set[str] = {r["name"] for r in all_records if r.get("name")}
    stale_names: set[str] = {r["name"] for r in stale_records if r.get("name")}

    roots: list[str] = []
    for r in stale_records:
        name = r.get("name")
        if not name:
            continue
        parent_names_in_set = {
            pn
            for p in (r.get("depends_on") or {}).get("nodes") or []
            for pn in [_name_from_unique_id(p)]
            if pn and pn in in_set
        }
        # If no parent in the build set is stale, this is a root we need to start from.
        if not any(pn in stale_names for pn in parent_names_in_set):
            roots.append(name)

    if not roots:
        return None

    selectors = " ".join(f"{r}+" for r in sorted(set(roots)))
    return f"dbts build --select {selectors}"
