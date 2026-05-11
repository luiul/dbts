from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from snowflake.connector import SnowflakeConnection

from dbts.snowflake import run_sql

log = logging.getLogger("dbts.cost")

# Snowflake credits per warehouse-hour, by warehouse size.
# https://docs.snowflake.com/en/user-guide/warehouses-overview
WAREHOUSE_CREDITS: dict[str, float] = {
    "X-SMALL": 1.0,
    "SMALL": 2.0,
    "MEDIUM": 4.0,
    "LARGE": 8.0,
    "X-LARGE": 16.0,
    "2X-LARGE": 32.0,
    "3X-LARGE": 64.0,
    "4X-LARGE": 128.0,
    "5X-LARGE": 256.0,
    "6X-LARGE": 512.0,
}

# Default credit-to-USD rate (Snowflake Standard Edition list price).
DEFAULT_CREDIT_RATE_USD = 3.00

# Default lookback window for QUERY_HISTORY. 7 days covers a typical week of
# dev iteration and is ~4x faster than the 30-day query.
DEFAULT_LOOKBACK_DAYS = 7

# When a model has no full-refresh history, multiply its incremental p50 by this
# to estimate full-refresh cost. Crude, but the user is told it's extrapolated.
FULL_REFRESH_MULTIPLIER = 8.0

MODE_INCREMENTAL = "incremental"
MODE_FULL_REFRESH = "full_refresh"


@dataclass(frozen=True)
class ModelStats:
    model: str
    mode: str  # "incremental" or "full_refresh"
    run_count: int
    p50_elapsed_s: float
    last_seen: datetime
    warehouse_size: str  # e.g. "MEDIUM"


@dataclass
class BuildEstimate:
    mode: str
    total_elapsed_s: float
    total_credits: float
    total_usd: float
    models_with_data: int
    models_extrapolated: int
    models_no_data: int


@dataclass
class CostReport:
    incremental: BuildEstimate
    full_refresh: BuildEstimate
    top_expensive: list[tuple[str, str, float, float, float]] = field(default_factory=list)
    # Each tuple: (model_name, mode_used_as_basis, elapsed_s, credits, usd)
    primary_warehouse: str | None = None
    primary_warehouse_size: str | None = None
    lookback_days: int = DEFAULT_LOOKBACK_DAYS


def credit_rate() -> float:
    raw = os.environ.get("DBTS_CREDIT_RATE")
    if not raw:
        return DEFAULT_CREDIT_RATE_USD
    try:
        return float(raw)
    except ValueError:
        log.warning(
            "[yellow]invalid $DBTS_CREDIT_RATE=%s, using default %.2f[/yellow]",
            raw,
            DEFAULT_CREDIT_RATE_USD,
        )
        return DEFAULT_CREDIT_RATE_USD


def credits_for(elapsed_s: float, warehouse_size: str | None) -> float:
    """Estimate credits consumed for a query of the given elapsed time."""
    size = (warehouse_size or "MEDIUM").upper()
    per_hour = WAREHOUSE_CREDITS.get(size, WAREHOUSE_CREDITS["MEDIUM"])
    return per_hour * (elapsed_s / 3600.0)


def format_duration(seconds: float) -> str:
    if seconds < 1:
        return f"{seconds * 1000:.0f}ms"
    if seconds < 60:
        return f"{seconds:.1f}s"
    if seconds < 3600:
        minutes = seconds / 60
        return f"{minutes:.1f} min"
    hours = int(seconds // 3600)
    rem_min = int((seconds % 3600) // 60)
    return f"{hours}h {rem_min}m"


def format_relative(ts: datetime, now: datetime | None = None) -> str:
    """Render a timestamp as a coarse 'N units ago' string."""
    now = now or datetime.now(tz=ts.tzinfo)
    delta_s = (now - ts).total_seconds()
    if delta_s < 60:
        return "just now"
    if delta_s < 3600:
        return f"{int(delta_s // 60)}m ago"
    if delta_s < 86400:
        return f"{int(delta_s // 3600)}h ago"
    days = int(delta_s // 86400)
    if days < 14:
        return f"{days}d ago"
    return ts.date().isoformat()


def fetch_history(
    conn: SnowflakeConnection,
    model_names: list[str],
    days: int = DEFAULT_LOOKBACK_DAYS,
) -> list[ModelStats]:
    """Query SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY for stats on the given models.

    Uses a `query_tag LIKE` prefilter so Snowflake can prune rows before the
    JSON parse, which is dramatically faster on large account-usage tables.
    """
    if not model_names:
        return []

    quoted = ",".join("'" + m.replace("'", "''") + "'" for m in model_names)
    like_clauses = " OR ".join(
        f'query_tag LIKE \'%"model": "{m.replace(chr(39), chr(39) + chr(39))}"%\'' for m in model_names
    )
    sql = f"""
        WITH candidates AS (
            SELECT *
            FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY
            WHERE start_time > DATEADD('day', -{int(days)}, CURRENT_TIMESTAMP())
              AND execution_status = 'SUCCESS'
              AND query_type IN ('CREATE_TABLE_AS_SELECT', 'INSERT', 'MERGE', 'CREATE_TABLE')
              AND query_tag IS NOT NULL
              AND ({like_clauses})
        ),
        recent AS (
            SELECT
                TRY_PARSE_JSON(query_tag):model::string             AS model,
                COALESCE(TRY_PARSE_JSON(query_tag):"full-refresh"::boolean, FALSE) AS full_refresh,
                COALESCE(TRY_PARSE_JSON(query_tag):incremental::boolean, FALSE)    AS is_incremental,
                warehouse_name,
                warehouse_size,
                total_elapsed_time / 1000.0                          AS elapsed_s,
                start_time
            FROM candidates
            WHERE TRY_PARSE_JSON(query_tag):model::string IN ({quoted})
        )
        SELECT
            model,
            CASE WHEN full_refresh OR NOT is_incremental
                 THEN '{MODE_FULL_REFRESH}'
                 ELSE '{MODE_INCREMENTAL}' END                        AS mode,
            COUNT(*)                                                  AS run_count,
            MEDIAN(elapsed_s)                                         AS p50_elapsed_s,
            MAX(start_time)                                           AS last_seen,
            MAX(warehouse_size)                                       AS warehouse_size
        FROM recent
        GROUP BY 1, 2
    """
    rows = run_sql(conn, sql)
    return [_row_to_stats(r) for r in rows]


def _row_to_stats(row: dict[str, Any]) -> ModelStats:
    return ModelStats(
        model=row["MODEL"],
        mode=row["MODE"],
        run_count=int(row["RUN_COUNT"]),
        p50_elapsed_s=float(row["P50_ELAPSED_S"]),
        last_seen=row["LAST_SEEN"],
        warehouse_size=(row.get("WAREHOUSE_SIZE") or "MEDIUM"),
    )


def index_stats(stats: list[ModelStats]) -> dict[str, dict[str, ModelStats]]:
    """Index as {model_name: {mode: ModelStats}}."""
    out: dict[str, dict[str, ModelStats]] = {}
    for s in stats:
        out.setdefault(s.model, {})[s.mode] = s
    return out


def estimate_build(
    model_names: list[str],
    stats_by_model: dict[str, dict[str, ModelStats]],
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> CostReport:
    """Aggregate per-model stats into per-mode totals + top-5 most expensive."""
    rate = credit_rate()
    incr = _estimate_for(model_names, stats_by_model, MODE_INCREMENTAL, rate)
    full = _estimate_for(model_names, stats_by_model, MODE_FULL_REFRESH, rate)

    # Top 5 most expensive on full-refresh basis. Use the same lookup logic
    # estimate_for uses (extrapolated from incremental if no FR data).
    rows: list[tuple[str, str, float, float, float]] = []
    for name in model_names:
        modes = stats_by_model.get(name) or {}
        elapsed_s, basis = _estimate_one(modes, MODE_FULL_REFRESH)
        if elapsed_s is None or basis is None:
            continue
        size = modes.get(basis) or modes.get(MODE_INCREMENTAL) or modes.get(MODE_FULL_REFRESH)
        ws = size.warehouse_size if size else "MEDIUM"
        credits = credits_for(elapsed_s, ws)
        rows.append((name, basis, elapsed_s, credits, credits * rate))
    rows.sort(key=lambda r: r[3], reverse=True)

    primary_wh, primary_size = _primary_warehouse(stats_by_model)
    return CostReport(
        incremental=incr,
        full_refresh=full,
        top_expensive=rows[:5],
        primary_warehouse=primary_wh,
        primary_warehouse_size=primary_size,
        lookback_days=lookback_days,
    )


def _estimate_for(
    model_names: list[str],
    stats_by_model: dict[str, dict[str, ModelStats]],
    mode: str,
    rate: float,
) -> BuildEstimate:
    total_s = 0.0
    total_credits = 0.0
    with_data = 0
    extrapolated = 0
    no_data = 0
    for name in model_names:
        modes = stats_by_model.get(name) or {}
        elapsed_s, basis = _estimate_one(modes, mode)
        if elapsed_s is None or basis is None:
            no_data += 1
            continue
        if basis != mode:
            extrapolated += 1
        else:
            with_data += 1
        size = modes.get(basis)
        ws = size.warehouse_size if size else "MEDIUM"
        total_s += elapsed_s
        total_credits += credits_for(elapsed_s, ws)
    return BuildEstimate(
        mode=mode,
        total_elapsed_s=total_s,
        total_credits=total_credits,
        total_usd=total_credits * rate,
        models_with_data=with_data,
        models_extrapolated=extrapolated,
        models_no_data=no_data,
    )


def _estimate_one(modes: dict[str, ModelStats], target_mode: str) -> tuple[float | None, str | None]:
    """Return (elapsed_s, basis_mode_used). Extrapolates full_refresh from incremental if needed."""
    direct = modes.get(target_mode)
    if direct:
        return direct.p50_elapsed_s, target_mode
    if target_mode == MODE_FULL_REFRESH:
        incr = modes.get(MODE_INCREMENTAL)
        if incr:
            return incr.p50_elapsed_s * FULL_REFRESH_MULTIPLIER, MODE_INCREMENTAL
    if target_mode == MODE_INCREMENTAL:
        # Don't extrapolate down — full-refresh time isn't a useful incremental proxy.
        return None, None
    return None, None


def _primary_warehouse(
    stats_by_model: dict[str, dict[str, ModelStats]],
) -> tuple[str | None, str | None]:
    """Pick the most common warehouse size across models. Returns (size, size) since the
    underlying Snowflake table only stores size, not name; we use size as the label."""
    sizes: dict[str, int] = {}
    for modes in stats_by_model.values():
        for s in modes.values():
            sizes[s.warehouse_size] = sizes.get(s.warehouse_size, 0) + 1
    if not sizes:
        return None, None
    primary = max(sizes.items(), key=lambda kv: kv[1])[0]
    return primary, primary
