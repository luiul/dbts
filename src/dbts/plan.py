from __future__ import annotations

import contextlib
import json
import logging
import subprocess
import threading
from collections import Counter, defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any

from rich.console import Console
from rich.table import Table

from dbts import cost, dbt_runner
from dbts.config import Target
from dbts.cost import (
    DEFAULT_LOOKBACK_DAYS,
    MODE_FULL_REFRESH,
    MODE_INCREMENTAL,
    BuildEstimate,
    CostReport,
    ModelStats,
)
from dbts.snowflake import connect

OUTPUT_KEYS = "name resource_type config tags original_file_path depends_on"

# Flags that are valid on `dbt build` / `run` etc. but not on `dbt ls`.
# `dbts plan` accepts the same surface as `dbts build` for ergonomics, then
# strips these before invoking `dbt ls` so the user can reuse their build
# command verbatim.
LS_INCOMPATIBLE_FLAGS: frozenset[str] = frozenset(
    {
        "--full-refresh",
        "--no-full-refresh",
        "--fail-fast",
        "--no-fail-fast",
        "--store-failures",
        "--no-store-failures",
        "--empty",
        "--sample",
        "--threads",
    }
)
LS_INCOMPATIBLE_FLAGS_WITH_VALUE: frozenset[str] = frozenset({"--threads"})

log = logging.getLogger("dbts.plan")
console = Console()


def run(
    args: Iterable[str],
    target_name: str,
    target: Target,
    with_cost: bool = False,
    days: int = DEFAULT_LOOKBACK_DAYS,
) -> int:
    """Preview the build set for the given selectors.

    Forwards `args` to `dbt ls --output json --resource-type model`, parses the
    JSON-lines output, and prints a grouped summary plus suggested exclusions.

    When `with_cost` is True, also queries SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY
    for per-model runtime stats and prints credit estimates plus a top-5 list.
    `days` controls the QUERY_HISTORY lookback window.

    `dbt ls` and the Snowflake auth+query run in parallel when `with_cost` is
    True, since they're independent.
    """
    dbt_runner.ensure_dbt_on_path()

    arg_list = _strip_ls_incompatible(list(args))
    cmd = [
        "dbt",
        "ls",
        "--target",
        target_name,
        "--output",
        "json",
        "--output-keys",
        OUTPUT_KEYS,
        *arg_list,
    ]
    if not _has_flag(arg_list, "--resource-type"):
        cmd.extend(["--resource-type", "model"])

    # Kick off Snowflake auth in parallel — it's independent of `dbt ls`.
    conn_holder: dict[str, Any] = {}
    conn_thread: threading.Thread | None = None
    if with_cost:
        log.info("[dim]Fetching cost data from Snowflake (last %d days)...[/dim]", days)
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
        if conn_thread is not None:
            conn_thread.join()
            _close(conn_holder.get("conn"))
        if completed.stderr:
            console.print(completed.stderr.rstrip(), style="red")
        if completed.stdout:
            console.print(completed.stdout.rstrip())
        return completed.returncode

    records = _parse_json_lines(completed.stdout)
    if not records:
        if conn_thread is not None:
            conn_thread.join()
            _close(conn_holder.get("conn"))
        log.warning("[yellow]Build set is empty[/yellow] — your selectors matched nothing.")
        return 0

    stats_by_model: dict[str, dict[str, ModelStats]] = {}
    cost_report: CostReport | None = None
    if with_cost and conn_thread is not None:
        stats_by_model, cost_report = _finish_cost(records, conn_thread, conn_holder, days)

    _render(records, stats_by_model, cost_report)
    return 0


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


def _has_flag(args: list[str], flag: str) -> bool:
    return any(a == flag or a.startswith(f"{flag}=") for a in args)


def _strip_ls_incompatible(args: list[str]) -> list[str]:
    """Drop flags that `dbt ls` doesn't accept (they're build-only)."""
    out: list[str] = []
    i = 0
    while i < len(args):
        tok = args[i]
        bare = tok.split("=", 1)[0]
        if bare in LS_INCOMPATIBLE_FLAGS:
            if "=" not in tok and bare in LS_INCOMPATIBLE_FLAGS_WITH_VALUE and i + 1 < len(args):
                i += 2
                continue
            i += 1
            continue
        out.append(tok)
        i += 1
    return out


def _parse_json_lines(stdout: str) -> list[dict]:
    out: list[dict] = []
    for raw in stdout.splitlines():
        line = raw.strip()
        if not line.startswith("{"):
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _materialization(record: dict) -> str:
    cfg = record.get("config") or {}
    return str(cfg.get("materialized") or "?")


def _model_deps(record: dict) -> int:
    deps = (record.get("depends_on") or {}).get("nodes") or []
    return sum(1 for n in deps if isinstance(n, str) and n.startswith("model."))


def _directory(record: dict) -> str:
    path = record.get("original_file_path") or ""
    parent = str(PurePosixPath(path).parent)
    return parent or "."


def _finish_cost(
    records: list[dict],
    conn_thread: threading.Thread,
    conn_holder: dict[str, Any],
    days: int,
) -> tuple[dict[str, dict[str, ModelStats]], CostReport | None]:
    """Wait for the parallel Snowflake auth, then run QUERY_HISTORY query."""
    model_names = [r["name"] for r in records if r.get("name")]
    if not model_names:
        conn_thread.join()
        _close(conn_holder.get("conn"))
        return {}, None

    conn_thread.join()
    if "error" in conn_holder:
        log.warning(
            "[yellow]Cost data unavailable:[/yellow] %s\n[dim]Continuing without cost estimates.[/dim]",
            _short_error(conn_holder["error"]),
        )
        return {}, None

    conn = conn_holder.get("conn")
    if conn is None:
        return {}, None
    try:
        stats = cost.fetch_history(conn, model_names, days=days)
    except Exception as e:  # snowflake.connector raises a zoo of types
        log.warning(
            "[yellow]Cost data unavailable:[/yellow] %s\n[dim]Continuing without cost estimates.[/dim]",
            _short_error(e),
        )
        return {}, None
    finally:
        _close(conn)

    if not stats:
        log.info("[dim]No matching query history found in the last %d days.[/dim]", days)
        return {}, None

    indexed = cost.index_stats(stats)
    report = cost.estimate_build(model_names, indexed, lookback_days=days)
    return indexed, report


def _short_error(e: Exception) -> str:
    msg = str(e).strip()
    first_line = msg.splitlines()[0] if msg else type(e).__name__
    return first_line[:200]


def _render(
    records: list[dict],
    stats_by_model: dict[str, dict[str, ModelStats]],
    cost_report: CostReport | None,
) -> None:
    by_dir: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        by_dir[_directory(r)].append(r)

    show_per_model_cost = bool(stats_by_model)
    show_tags = any(r.get("tags") for r in records)
    now_utc = datetime.now(tz=UTC)

    for directory in sorted(by_dir):
        models = sorted(by_dir[directory], key=lambda r: r.get("name") or "")
        count = len(models)
        console.print(f"[bold cyan]{directory}[/bold cyan] [dim]({count} model{'s' if count != 1 else ''})[/dim]")
        table = Table(
            show_header=True,
            header_style="dim",
            box=None,
            pad_edge=False,
            padding=(0, 2),
        )
        table.add_column("model", overflow="fold")
        table.add_column("materialization")
        if show_tags:
            table.add_column("tags", overflow="fold")
        table.add_column("parents", justify="right")
        if show_per_model_cost:
            table.add_column("p50 incr", justify="right")
            table.add_column("last seen", justify="right")
        for r in models:
            name = r.get("name") or "?"
            row = [
                name,
                _materialization(r),
            ]
            if show_tags:
                row.append(",".join(sorted(r.get("tags") or [])) or "-")
            row.append(str(_model_deps(r)))
            if show_per_model_cost:
                modes = stats_by_model.get(name) or {}
                incr = modes.get(MODE_INCREMENTAL) or modes.get(MODE_FULL_REFRESH)
                if incr:
                    row.append(cost.format_duration(incr.p50_elapsed_s))
                    row.append(cost.format_relative(incr.last_seen, now_utc))
                else:
                    row.append("[dim]-[/dim]")
                    row.append("[dim]no data[/dim]")
            table.add_row(*row)
        console.print(table)
        console.print()

    _print_footer(records, by_dir, cost_report)


def _print_footer(
    records: list[dict],
    by_dir: dict[str, list[dict]],
    cost_report: CostReport | None,
) -> None:
    mat_counts: Counter[str] = Counter(_materialization(r) for r in records)
    console.print(
        f"[bold]Build set:[/bold] {len(records)} model{'s' if len(records) != 1 else ''} "
        f"across {len(by_dir)} director{'ies' if len(by_dir) != 1 else 'y'}"
    )
    for mat, count in sorted(mat_counts.items(), key=lambda kv: (-kv[1], kv[0])):
        console.print(f"  {mat:14s} {count}")

    if cost_report is not None:
        _print_cost(cost_report, cost_report.lookback_days)

    excludes = _suggest_excludes(records, by_dir)
    if not excludes:
        return
    console.print()
    console.print("[bold]Quick excludes:[/bold] [dim](copy-paste into your dbts build command)[/dim]")
    for snippet, count in excludes:
        console.print(f"  [dim]({count:3d} model{' ' if count == 1 else 's'})[/dim] --exclude {snippet}")


def _print_cost(report: CostReport, days: int) -> None:
    console.print()
    header = f"[bold]Estimated cost[/bold] [dim](last-{days}d median"
    if report.primary_warehouse_size:
        header += f", warehouse {report.primary_warehouse_size}"
    header += ")[/dim]"
    console.print(header)
    _print_estimate("Incremental ", report.incremental)
    _print_estimate("Full refresh", report.full_refresh)
    if report.full_refresh.models_extrapolated:
        console.print(
            f"  [dim]({report.full_refresh.models_extrapolated} model"
            f"{'s' if report.full_refresh.models_extrapolated != 1 else ''} "
            f"extrapolated from incremental p50 x {int(cost.FULL_REFRESH_MULTIPLIER)})[/dim]"
        )

    if report.top_expensive:
        console.print()
        console.print("[bold]Top 5 most expensive[/bold] [dim](on full-refresh basis)[/dim]")
        for name, basis, elapsed_s, credits, usd in report.top_expensive:
            arrow = " " if basis == MODE_FULL_REFRESH else "*"
            console.print(
                f"  {arrow} {name:50s} {cost.format_duration(elapsed_s):>10s}  {credits:>5.2f} credits  ~${usd:>5.2f}"
            )
        if any(b != MODE_FULL_REFRESH for _, b, _, _, _ in report.top_expensive):
            console.print("  [dim]* extrapolated from incremental p50[/dim]")


def _print_estimate(label: str, est: BuildEstimate) -> None:
    coverage_total = est.models_with_data + est.models_extrapolated + est.models_no_data
    coverage = f"{est.models_with_data + est.models_extrapolated}/{coverage_total} models"
    if est.models_extrapolated:
        coverage += f" (~{est.models_extrapolated} extrapolated)"
    console.print(
        f"  {label}: "
        f"{cost.format_duration(est.total_elapsed_s):>10s}  "
        f"{est.total_credits:>6.2f} credits  ~${est.total_usd:>6.2f}  "
        f"[dim]({coverage})[/dim]"
    )


def _suggest_excludes(records: list[dict], by_dir: dict[str, list[dict]]) -> list[tuple[str, int]]:
    total = len(records)
    suggestions: list[tuple[str, int]] = []

    for directory, models in sorted(by_dir.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        if directory == "." or len(models) < 2 or len(models) >= total:
            continue
        suggestions.append((f"path:{directory}", len(models)))

    tag_counts: Counter[str] = Counter()
    for r in records:
        for t in r.get("tags") or []:
            tag_counts[t] += 1
    for tag, count in sorted(tag_counts.items(), key=lambda kv: (-kv[1], kv[0])):
        if count < 3 or count >= total:
            continue
        suggestions.append((f"tag:{tag}", count))

    return suggestions[:8]
