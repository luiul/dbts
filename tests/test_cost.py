from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from dbts.cost import (
    DEFAULT_CREDIT_RATE_USD,
    FULL_REFRESH_MULTIPLIER,
    MODE_FULL_REFRESH,
    MODE_INCREMENTAL,
    WAREHOUSE_CREDITS,
    BuildEstimate,
    ModelStats,
    credit_rate,
    credits_for,
    estimate_build,
    format_duration,
    format_relative,
    index_stats,
)


def _stats(
    model: str,
    mode: str,
    p50: float,
    size: str = "MEDIUM",
    last_seen: datetime | None = None,
) -> ModelStats:
    return ModelStats(
        model=model,
        mode=mode,
        run_count=3,
        p50_elapsed_s=p50,
        last_seen=last_seen or datetime.now(tz=UTC),
        warehouse_size=size,
    )


# ---------------------------- credit_rate ---------------------------------- #


def test_credit_rate_uses_default_when_unset(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("DBTS_CREDIT_RATE", raising=False)
    assert credit_rate() == DEFAULT_CREDIT_RATE_USD


def test_credit_rate_reads_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DBTS_CREDIT_RATE", "4.50")
    assert credit_rate() == 4.50


def test_credit_rate_falls_back_on_invalid_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DBTS_CREDIT_RATE", "not-a-number")
    assert credit_rate() == DEFAULT_CREDIT_RATE_USD


# ---------------------------- credits_for ---------------------------------- #


def test_credits_for_xsmall_one_hour():
    assert credits_for(3600.0, "X-SMALL") == pytest.approx(1.0)


def test_credits_for_medium_one_hour():
    assert credits_for(3600.0, "MEDIUM") == pytest.approx(4.0)


def test_credits_for_large_30_minutes():
    assert credits_for(1800.0, "LARGE") == pytest.approx(4.0)


def test_credits_for_unknown_size_falls_back_to_medium():
    assert credits_for(3600.0, "FROBNICATOR") == pytest.approx(WAREHOUSE_CREDITS["MEDIUM"])


def test_credits_for_none_size_falls_back_to_medium():
    assert credits_for(3600.0, None) == pytest.approx(WAREHOUSE_CREDITS["MEDIUM"])


def test_credits_for_lowercase_size_normalized():
    assert credits_for(3600.0, "medium") == pytest.approx(WAREHOUSE_CREDITS["MEDIUM"])


# ---------------------------- format_duration ------------------------------ #


def test_format_duration_sub_second():
    assert format_duration(0.5) == "500ms"


def test_format_duration_seconds():
    assert format_duration(12.4) == "12.4s"


def test_format_duration_minutes():
    assert format_duration(150.0) == "2.5 min"


def test_format_duration_hours():
    assert format_duration(7250.0) == "2h 0m"


# ---------------------------- format_relative ------------------------------ #


def test_format_relative_just_now():
    now = datetime.now(tz=UTC)
    assert format_relative(now - timedelta(seconds=5), now=now) == "just now"


def test_format_relative_minutes():
    now = datetime.now(tz=UTC)
    assert format_relative(now - timedelta(minutes=15), now=now) == "15m ago"


def test_format_relative_hours():
    now = datetime.now(tz=UTC)
    assert format_relative(now - timedelta(hours=3), now=now) == "3h ago"


def test_format_relative_days():
    now = datetime.now(tz=UTC)
    assert format_relative(now - timedelta(days=5), now=now) == "5d ago"


def test_format_relative_long_ago_uses_iso_date():
    now = datetime.now(tz=UTC)
    past = now - timedelta(days=20)
    assert format_relative(past, now=now) == past.date().isoformat()


# ---------------------------- index_stats ---------------------------------- #


def test_index_stats_groups_by_model_and_mode():
    s_a_incr = _stats("a", MODE_INCREMENTAL, 10.0)
    s_a_full = _stats("a", MODE_FULL_REFRESH, 80.0)
    s_b_incr = _stats("b", MODE_INCREMENTAL, 5.0)
    indexed = index_stats([s_a_incr, s_a_full, s_b_incr])
    assert indexed["a"][MODE_INCREMENTAL] is s_a_incr
    assert indexed["a"][MODE_FULL_REFRESH] is s_a_full
    assert indexed["b"][MODE_INCREMENTAL] is s_b_incr
    assert MODE_FULL_REFRESH not in indexed["b"]


# ---------------------------- estimate_build ------------------------------- #


def test_estimate_build_uses_direct_data_when_present(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DBTS_CREDIT_RATE", "3.00")
    indexed = index_stats(
        [
            _stats("a", MODE_INCREMENTAL, 60.0, size="MEDIUM"),
            _stats("a", MODE_FULL_REFRESH, 600.0, size="MEDIUM"),
            _stats("b", MODE_INCREMENTAL, 30.0, size="MEDIUM"),
            _stats("b", MODE_FULL_REFRESH, 240.0, size="MEDIUM"),
        ]
    )
    report = estimate_build(["a", "b"], indexed)

    assert isinstance(report.incremental, BuildEstimate)
    assert report.incremental.total_elapsed_s == pytest.approx(90.0)
    # 90s on MEDIUM: 4 credits/hr * (90/3600) = 0.1
    assert report.incremental.total_credits == pytest.approx(0.1)
    assert report.incremental.models_with_data == 2
    assert report.incremental.models_extrapolated == 0

    assert report.full_refresh.total_elapsed_s == pytest.approx(840.0)
    assert report.full_refresh.models_with_data == 2


def test_estimate_build_extrapolates_full_refresh_from_incremental(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DBTS_CREDIT_RATE", "3.00")
    indexed = index_stats([_stats("a", MODE_INCREMENTAL, 10.0, size="MEDIUM")])
    report = estimate_build(["a"], indexed)
    expected_full_s = 10.0 * FULL_REFRESH_MULTIPLIER
    assert report.full_refresh.total_elapsed_s == pytest.approx(expected_full_s)
    assert report.full_refresh.models_extrapolated == 1
    assert report.full_refresh.models_with_data == 0


def test_estimate_build_no_data_models_excluded():
    indexed = index_stats([_stats("a", MODE_INCREMENTAL, 60.0)])
    report = estimate_build(["a", "b", "c"], indexed)
    # b and c have no stats at all
    assert report.incremental.models_no_data == 2
    assert report.incremental.models_with_data == 1
    assert report.incremental.total_elapsed_s == pytest.approx(60.0)


def test_estimate_build_top_expensive_sorted_descending():
    indexed = index_stats(
        [
            _stats("cheap", MODE_FULL_REFRESH, 5.0),
            _stats("expensive", MODE_FULL_REFRESH, 600.0),
            _stats("middle", MODE_FULL_REFRESH, 100.0),
        ]
    )
    report = estimate_build(["cheap", "expensive", "middle"], indexed)
    names_in_order = [name for name, _, _, _, _ in report.top_expensive]
    assert names_in_order == ["expensive", "middle", "cheap"]


def test_estimate_build_primary_warehouse_is_most_common():
    indexed = index_stats(
        [
            _stats("a", MODE_INCREMENTAL, 60.0, size="MEDIUM"),
            _stats("b", MODE_INCREMENTAL, 60.0, size="MEDIUM"),
            _stats("c", MODE_INCREMENTAL, 60.0, size="LARGE"),
        ]
    )
    report = estimate_build(["a", "b", "c"], indexed)
    assert report.primary_warehouse_size == "MEDIUM"


def test_estimate_build_empty_inputs():
    report = estimate_build([], {})
    assert report.incremental.models_with_data == 0
    assert report.full_refresh.total_credits == pytest.approx(0.0)
    assert report.top_expensive == []
    assert report.primary_warehouse_size is None
