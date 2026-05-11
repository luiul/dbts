from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from dbts.freshness import (
    DEFAULT_STALE_WINDOW,
    TableInfo,
    _compute_threshold,
    _env_postfix,
    _physical,
    _suggest_rebuild,
    parse_since,
    topological_sort,
)


def _model(name: str, parents: list[str] | None = None, project: str = "proj") -> dict:
    return {
        "name": name,
        "depends_on": {"nodes": [f"model.{project}.{p}" for p in (parents or [])]},
    }


def _info(
    db: str,
    schema: str,
    name: str,
    last_altered: datetime | None,
    rows: int | None = None,
) -> TableInfo:
    return TableInfo(
        database=db.upper(),
        schema=schema.upper(),
        name=name.upper(),
        last_altered=last_altered,
        row_count=rows,
    )


# ----- topological_sort ----------------------------------------------------- #


def test_topo_linear_chain():
    records = [
        _model("c", ["b"]),
        _model("a"),
        _model("b", ["a"]),
    ]
    result = [r["name"] for r in topological_sort(records)]
    assert result == ["a", "b", "c"]


def test_topo_diamond():
    records = [
        _model("d", ["b", "c"]),
        _model("b", ["a"]),
        _model("c", ["a"]),
        _model("a"),
    ]
    result = [r["name"] for r in topological_sort(records)]
    # Roots first, then b/c (alphabetical), then d.
    assert result[0] == "a"
    assert result[-1] == "d"
    assert set(result[1:3]) == {"b", "c"}
    assert result[1:3] == sorted(result[1:3])  # alphabetical tiebreak


def test_topo_multiple_roots():
    records = [_model("r1"), _model("r2"), _model("r3")]
    result = [r["name"] for r in topological_sort(records)]
    assert result == ["r1", "r2", "r3"]


def test_topo_external_parent_treated_as_satisfied():
    # `b`'s parent is outside the build set; it should still appear in topo order.
    records = [
        _model("b", ["external_source"]),
        _model("a"),
    ]
    result = [r["name"] for r in topological_sort(records)]
    assert set(result) == {"a", "b"}


def test_topo_preserves_unknown_project_namespace_records():
    # Records with no parents in the build set still get a unique_id so they're
    # included.
    records = [_model("solo")]
    result = [r["name"] for r in topological_sort(records)]
    assert result == ["solo"]


# ----- parse_since ---------------------------------------------------------- #


def test_parse_since_relative_hours():
    now = datetime.now(tz=UTC)
    parsed = parse_since("24h")
    assert (now - timedelta(hours=24)) - parsed < timedelta(seconds=2)


def test_parse_since_relative_days():
    now = datetime.now(tz=UTC)
    parsed = parse_since("7d")
    assert (now - timedelta(days=7)) - parsed < timedelta(seconds=2)


def test_parse_since_relative_weeks():
    now = datetime.now(tz=UTC)
    parsed = parse_since("1w")
    assert (now - timedelta(weeks=1)) - parsed < timedelta(seconds=2)


def test_parse_since_iso_datetime_with_tz():
    parsed = parse_since("2026-05-09T17:00:00Z")
    assert parsed == datetime(2026, 5, 9, 17, 0, 0, tzinfo=UTC)


def test_parse_since_iso_datetime_naive_assumed_utc():
    parsed = parse_since("2026-05-09 17:00")
    assert parsed == datetime(2026, 5, 9, 17, 0, 0, tzinfo=UTC)


def test_parse_since_iso_date():
    parsed = parse_since("2026-05-09")
    assert parsed == datetime(2026, 5, 9, 0, 0, 0, tzinfo=UTC)


def test_parse_since_invalid_raises():
    with pytest.raises(ValueError, match="unparseable"):
        parse_since("yesterday")


# ----- _compute_threshold --------------------------------------------------- #


def test_compute_threshold_adaptive():
    fresh = datetime(2026, 5, 12, 14, 0, tzinfo=UTC)
    older = datetime(2026, 5, 10, 14, 0, tzinfo=UTC)
    infos = [
        _info("DB", "S", "T1", fresh),
        _info("DB", "S", "T2", older),
    ]
    threshold, baseline = _compute_threshold(infos, since=None)
    assert baseline == fresh
    assert threshold == fresh - DEFAULT_STALE_WINDOW


def test_compute_threshold_explicit_since_overrides():
    fresh = datetime(2026, 5, 12, 14, 0, tzinfo=UTC)
    infos = [_info("DB", "S", "T1", fresh)]
    threshold, baseline = _compute_threshold(infos, since="2026-05-09T17:00:00Z")
    # Baseline is still the freshest seen.
    assert baseline == fresh
    # Threshold comes from the explicit override.
    assert threshold == datetime(2026, 5, 9, 17, 0, tzinfo=UTC)


def test_compute_threshold_no_data_returns_none():
    threshold, baseline = _compute_threshold([], since=None)
    assert threshold is None
    assert baseline is None


# ----- _suggest_rebuild ----------------------------------------------------- #


def test_suggest_rebuild_single_root():
    a = _model("a")
    b = _model("b", ["a"])
    c = _model("c", ["b"])
    # b and c are stale; a is fresh. Suggested root is b.
    suggestion = _suggest_rebuild([b, c], [a, b, c])
    assert suggestion == "dbts build --select b+"


def test_suggest_rebuild_multiple_roots():
    a = _model("a")
    b = _model("b", ["a"])
    x = _model("x")
    y = _model("y", ["x"])
    # b and y stale, but their parents (a, x) are fresh. Two independent roots.
    suggestion = _suggest_rebuild([b, y], [a, b, x, y])
    assert suggestion == "dbts build --select b+ y+"


def test_suggest_rebuild_skips_stale_parents():
    # If b is stale AND its parent a is also stale, only a should be the root
    # (building a+ catches b too).
    a = _model("a")
    b = _model("b", ["a"])
    suggestion = _suggest_rebuild([a, b], [a, b])
    assert suggestion == "dbts build --select a+"


def test_suggest_rebuild_empty_returns_none():
    assert _suggest_rebuild([], []) is None


# ----- _env_postfix --------------------------------------------------------- #


def test_env_postfix_live_is_empty():
    assert _env_postfix("live", None) == ""


def test_env_postfix_dev():
    assert _env_postfix("dev", None) == "_DEV"


def test_env_postfix_staging():
    assert _env_postfix("staging", None) == "_STAGING"


def test_env_postfix_sandbox_with_user():
    assert _env_postfix("sandbox", "luis_aceituno") == "_SANDBOX_LUIS_ACEITUNO"


def test_env_postfix_sandbox_without_user_returns_empty():
    # No sandbox_user → can't construct the postfix; return "" so we fall back
    # to the static config.database (better than building a wrong name).
    assert _env_postfix("sandbox", None) == ""


def test_env_postfix_case_insensitive_target():
    assert _env_postfix("STAGING", None) == "_STAGING"


# ----- _physical (database rewrite) ----------------------------------------- #


def _record(database: str, schema: str = "RAW", name: str = "my_model") -> dict:
    return {
        "name": name,
        "config": {"database": database, "schema": schema},
    }


def test_physical_live_passes_through_unchanged():
    db, schema, name = _physical(_record("ANALYTICS"), "live", None)
    assert db == "ANALYTICS"
    assert schema == "RAW"
    assert name == "MY_MODEL"


def test_physical_sandbox_appends_user_postfix():
    db, _, _ = _physical(_record("SCM_ANALYTICS"), "sandbox", "luis_aceituno")
    assert db == "SCM_ANALYTICS_SANDBOX_LUIS_ACEITUNO"


def test_physical_staging_appends_postfix():
    db, _, _ = _physical(_record("SCM_ANALYTICS"), "staging", None)
    assert db == "SCM_ANALYTICS_STAGING"


def test_physical_sensitive_database_postfix_inserted_correctly():
    # Convention: ANALYTICS_SENSITIVE → ANALYTICS_STAGING_SENSITIVE
    db, _, _ = _physical(_record("ANALYTICS_SENSITIVE"), "staging", None)
    assert db == "ANALYTICS_STAGING_SENSITIVE"


def test_physical_uses_alias_when_set():
    record = {
        "name": "model_a",
        "config": {"database": "DB", "schema": "S", "alias": "renamed"},
    }
    _, _, name = _physical(record, "live", None)
    assert name == "RENAMED"
