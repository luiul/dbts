"""Helpers shared by commands that wrap `dbt ls`."""

from __future__ import annotations

import json

# Flags that are valid on `dbt build` / `run` etc. but not on `dbt ls`.
# Commands that wrap `dbt ls` accept the same surface as `dbts build` for
# ergonomics, then strip these before invoking `dbt ls` so a working
# `dbts build` invocation can be reused verbatim.
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

# Output keys requested via `dbt ls --output-keys`. Kept in one place so all
# `dbt ls`-wrapping commands consume the same record shape.
DEFAULT_OUTPUT_KEYS = "name resource_type config tags original_file_path depends_on"


def has_flag(args: list[str], flag: str) -> bool:
    return any(a == flag or a.startswith(f"{flag}=") for a in args)


def strip_ls_incompatible(args: list[str]) -> list[str]:
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


def parse_json_lines(stdout: str) -> list[dict]:
    """Parse `dbt ls --output json` output, ignoring non-JSON log lines."""
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
