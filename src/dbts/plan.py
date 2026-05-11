from __future__ import annotations

import json
import logging
import subprocess
from collections import Counter, defaultdict
from collections.abc import Iterable
from pathlib import PurePosixPath

from rich.console import Console
from rich.table import Table

from dbts import dbt_runner
from dbts.config import Target

OUTPUT_KEYS = "name resource_type config tags original_file_path depends_on"

log = logging.getLogger("dbts.plan")
console = Console()


def run(args: Iterable[str], target_name: str, target: Target) -> int:
    """Preview the build set for the given selectors.

    Forwards `args` to `dbt ls --output json --resource-type model`, parses the
    JSON-lines output, and prints a grouped summary plus suggested exclusions.
    """
    dbt_runner.ensure_dbt_on_path()

    arg_list = list(args)
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

    completed = subprocess.run(
        cmd,
        cwd=str(dbt_runner.project_root()),
        env=dbt_runner.dbt_env(target_name, target),
        capture_output=True,
        text=True,
        check=False,
    )

    if completed.returncode != 0:
        if completed.stderr:
            console.print(completed.stderr.rstrip(), style="red")
        if completed.stdout:
            console.print(completed.stdout.rstrip())
        return completed.returncode

    records = _parse_json_lines(completed.stdout)
    if not records:
        log.warning("[yellow]Build set is empty[/yellow] — your selectors matched nothing.")
        return 0

    _render(records)
    return 0


def _has_flag(args: list[str], flag: str) -> bool:
    return any(a == flag or a.startswith(f"{flag}=") for a in args)


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


def _render(records: list[dict]) -> None:
    by_dir: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        by_dir[_directory(r)].append(r)

    for directory in sorted(by_dir):
        models = sorted(by_dir[directory], key=lambda r: r.get("name") or "")
        count = len(models)
        console.print(f"[bold cyan]{directory}[/bold cyan] [dim]({count} model{'s' if count != 1 else ''})[/dim]")
        table = Table(show_header=False, box=None, pad_edge=False, padding=(0, 2))
        table.add_column("model", overflow="fold")
        table.add_column("materialization")
        table.add_column("tags", overflow="fold")
        table.add_column("parents", justify="right")
        for r in models:
            tags = ",".join(sorted(r.get("tags") or [])) or "-"
            table.add_row(
                r.get("name") or "?",
                _materialization(r),
                tags,
                str(_model_deps(r)),
            )
        console.print(table)
        console.print()

    _print_footer(records, by_dir)


def _print_footer(records: list[dict], by_dir: dict[str, list[dict]]) -> None:
    mat_counts: Counter[str] = Counter(_materialization(r) for r in records)
    console.print(
        f"[bold]Build set:[/bold] {len(records)} model{'s' if len(records) != 1 else ''} "
        f"across {len(by_dir)} director{'ies' if len(by_dir) != 1 else 'y'}"
    )
    for mat, count in sorted(mat_counts.items(), key=lambda kv: (-kv[1], kv[0])):
        console.print(f"  {mat:14s} {count}")

    excludes = _suggest_excludes(records, by_dir)
    if not excludes:
        return
    console.print()
    console.print("[bold]Quick excludes:[/bold] [dim](copy-paste into your dbts build command)[/dim]")
    for snippet, count in excludes:
        console.print(f"  [dim]({count:3d} model{' ' if count == 1 else 's'})[/dim] --exclude {snippet}")


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
