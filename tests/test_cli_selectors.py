from __future__ import annotations

from dbts.cli import _promote_selectors


def test_bare_positional_promoted():
    assert _promote_selectors("build", ["my_model+"]) == ["--select", "my_model+"]


def test_bare_positional_with_flag_preserved():
    # Flag is not a selector flag, just a passthrough; positional becomes --select
    assert _promote_selectors("build", ["my_model+", "--full-refresh"]) == [
        "--select",
        "my_model+",
        "--full-refresh",
    ]


def test_multiple_bare_positionals_each_get_a_select_flag():
    # Repeated `--select` is unioned by dbt.
    assert _promote_selectors("build", ["+a+", "+b"]) == [
        "--select",
        "+a+",
        "--select",
        "+b",
    ]


def test_flag_with_value_consumes_next_token():
    # `--exclude foo` consumes `foo`, and the trailing bare positional attaches to
    # `--exclude` since that's the most recent selector flag.
    assert _promote_selectors("build", ["--exclude", "foo", "my_model+"]) == [
        "--exclude",
        "foo",
        "--exclude",
        "my_model+",
    ]


def test_explicit_select_with_extra_positional_attaches_to_select():
    # `--select a b` → `--select a --select b` (dbt unions the values)
    assert _promote_selectors("build", ["--select", "a", "b"]) == [
        "--select",
        "a",
        "--select",
        "b",
    ]


def test_explicit_short_select_extends_to_short_select():
    assert _promote_selectors("build", ["-s", "a", "b+"]) == ["-s", "a", "-s", "b+"]


def test_exclude_extends_to_exclude_not_select():
    # Regression: `--exclude foo bar` used to silently promote `bar` to --select.
    assert _promote_selectors("build", ["--exclude", "errors_reported", "cc_errors_reported"]) == [
        "--exclude",
        "errors_reported",
        "--exclude",
        "cc_errors_reported",
    ]


def test_mixed_select_then_exclude_each_attaches_to_its_own():
    # `foo --exclude bar baz` → `--select foo --exclude bar --exclude baz`
    assert _promote_selectors("build", ["foo", "--exclude", "bar", "baz"]) == [
        "--select",
        "foo",
        "--exclude",
        "bar",
        "--exclude",
        "baz",
    ]


def test_full_problem_command_shape():
    # Mirrors the user's actual failing invocation:
    # dbts plan a+ b+ c+ --target live --exclude errors_reported cc_errors_reported --full-refresh
    args = [
        "a+",
        "b+",
        "c+",
        "--target",
        "live",
        "--exclude",
        "errors_reported",
        "cc_errors_reported",
        "--full-refresh",
    ]
    assert _promote_selectors("build", args) == [
        "--select",
        "a+",
        "--select",
        "b+",
        "--select",
        "c+",
        "--target",
        "live",
        "--exclude",
        "errors_reported",
        "--exclude",
        "cc_errors_reported",
        "--full-refresh",
    ]


def test_non_selector_subcommand_left_untouched():
    assert _promote_selectors("debug", ["some_arg"]) == ["some_arg"]


def test_empty_args_returned_as_is():
    assert _promote_selectors("build", []) == []


def test_no_positionals_returns_args_unchanged():
    args = ["--select", "my_model+", "--full-refresh"]
    assert _promote_selectors("build", args) == args


def test_flag_with_equals_does_not_consume_next_token():
    # `--exclude=foo` is a single arg; `bar+` is a bare positional → --select (default).
    assert _promote_selectors("build", ["--exclude=foo", "bar+"]) == [
        "--exclude=foo",
        "--select",
        "bar+",
    ]


def test_target_flag_does_not_change_selector_mode():
    # `--target` consumes its value but isn't a selector; positionals after it stay --select.
    assert _promote_selectors("build", ["--target", "live", "foo+"]) == [
        "--target",
        "live",
        "--select",
        "foo+",
    ]
