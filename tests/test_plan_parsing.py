from __future__ import annotations

from dbts.plan import (
    _directory,
    _materialization,
    _model_deps,
    _parse_json_lines,
    _suggest_excludes,
)


def test_parse_skips_log_prefix_lines():
    stdout = (
        "[0m18:04:45  Running with dbt=1.9.7\n"
        "[0m18:04:45  Registered adapter: snowflake=1.9.4\n"
        '{"name": "model_a", "resource_type": "model"}\n'
        '{"name": "model_b", "resource_type": "model"}\n'
    )
    records = _parse_json_lines(stdout)
    assert [r["name"] for r in records] == ["model_a", "model_b"]


def test_parse_skips_malformed_json():
    stdout = '{"name": "ok"}\n{"broken: true\n{"name": "ok2"}\n'
    assert [r["name"] for r in _parse_json_lines(stdout)] == ["ok", "ok2"]


def test_parse_empty_input():
    assert _parse_json_lines("") == []


def test_directory_at_project_root():
    assert _directory({"original_file_path": "model.sql"}) == "."


def test_directory_extracts_parent():
    assert _directory({"original_file_path": "models/staging/foo.sql"}) == "models/staging"


def test_directory_missing_path_returns_dot():
    assert _directory({}) == "."


def test_materialization_extracted_from_config():
    assert _materialization({"config": {"materialized": "incremental"}}) == "incremental"


def test_materialization_defaults_to_question_mark():
    assert _materialization({}) == "?"
    assert _materialization({"config": {}}) == "?"


def test_model_deps_counts_only_model_nodes():
    rec = {
        "depends_on": {
            "nodes": [
                "model.proj.upstream_a",
                "source.proj.raw",
                "model.proj.upstream_b",
                "macro.proj.helper",
            ]
        }
    }
    assert _model_deps(rec) == 2


def test_model_deps_handles_missing_depends_on():
    assert _model_deps({}) == 0
    assert _model_deps({"depends_on": {}}) == 0


def _make_records(*specs: tuple[str, str, list[str]]) -> list[dict]:
    return [
        {
            "name": name,
            "original_file_path": path,
            "config": {"materialized": "table"},
            "tags": tags,
            "depends_on": {"nodes": []},
        }
        for name, path, tags in specs
    ]


def _by_dir(records: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for r in records:
        out.setdefault(_directory(r), []).append(r)
    return out


def test_suggest_excludes_filters_single_model_dirs():
    records = _make_records(
        ("a", "models/x/a.sql", []),
        ("b", "models/x/b.sql", []),
        ("c", "models/x/c.sql", []),
        ("d", "models/y/d.sql", []),  # only model in models/y, should be skipped
    )
    suggestions = _suggest_excludes(records, _by_dir(records))
    paths = [s for s, _ in suggestions]
    assert "path:models/x" in paths
    assert "path:models/y" not in paths


def test_suggest_excludes_filters_total_set_dir():
    records = _make_records(
        ("a", "models/x/a.sql", []),
        ("b", "models/x/b.sql", []),
        ("c", "models/x/c.sql", []),
    )
    # All 3 models are in models/x — excluding it would empty the build set, useless.
    suggestions = _suggest_excludes(records, _by_dir(records))
    assert all(s != "path:models/x" for s, _ in suggestions)


def test_suggest_excludes_includes_tags_with_three_or_more_models():
    records = _make_records(
        ("a", "models/x/a.sql", ["slow"]),
        ("b", "models/y/b.sql", ["slow"]),
        ("c", "models/z/c.sql", ["slow"]),
        ("d", "models/w/d.sql", ["fast"]),
    )
    suggestions = _suggest_excludes(records, _by_dir(records))
    snippets = [s for s, _ in suggestions]
    assert "tag:slow" in snippets
    assert "tag:fast" not in snippets  # below threshold


def test_suggest_excludes_filters_tag_present_on_all_models():
    records = _make_records(
        ("a", "models/x/a.sql", ["everywhere"]),
        ("b", "models/y/b.sql", ["everywhere"]),
        ("c", "models/z/c.sql", ["everywhere"]),
    )
    suggestions = _suggest_excludes(records, _by_dir(records))
    assert all(s != "tag:everywhere" for s, _ in suggestions)


def test_suggest_excludes_sorted_by_descending_count():
    records = _make_records(
        ("a", "models/big/a.sql", []),
        ("b", "models/big/b.sql", []),
        ("c", "models/big/c.sql", []),
        ("d", "models/big/d.sql", []),
        ("e", "models/small/e.sql", []),
        ("f", "models/small/f.sql", []),
        ("g", "models/lone/g.sql", []),
    )
    suggestions = _suggest_excludes(records, _by_dir(records))
    counts = [c for _, c in suggestions]
    assert counts == sorted(counts, reverse=True)
