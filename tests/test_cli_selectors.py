from __future__ import annotations

from dbts.cli import _promote_selectors


def test_bare_positional_promoted():
    assert _promote_selectors("build", ["my_model+"]) == ["--select", "my_model+"]


def test_bare_positional_with_flag_preserved():
    assert _promote_selectors("build", ["my_model+", "--full-refresh"]) == [
        "--full-refresh",
        "--select",
        "my_model+",
    ]


def test_multiple_positionals_joined():
    assert _promote_selectors("build", ["+a+", "+b"]) == ["--select", "+a+ +b"]


def test_flag_with_value_consumes_next_token():
    # `--exclude foo` should NOT swallow `my_model+` as the value
    assert _promote_selectors("build", ["--exclude", "foo", "my_model+"]) == [
        "--exclude",
        "foo",
        "--select",
        "my_model+",
    ]


def test_explicit_select_with_extra_positional_combined():
    # User typed `--select a b` — `a` is the --select value, `b` becomes a new --select
    assert _promote_selectors("build", ["--select", "a", "b"]) == [
        "--select",
        "a",
        "--select",
        "b",
    ]


def test_explicit_short_select_with_extra_positional_combined():
    assert _promote_selectors("build", ["-s", "a", "b+"]) == ["-s", "a", "--select", "b+"]


def test_non_selector_subcommand_left_untouched():
    assert _promote_selectors("debug", ["some_arg"]) == ["some_arg"]


def test_empty_args_returned_as_is():
    assert _promote_selectors("build", []) == []


def test_no_positionals_returns_args_unchanged():
    args = ["--select", "my_model+", "--full-refresh"]
    assert _promote_selectors("build", args) == args


def test_flag_with_equals_does_not_consume_next_token():
    # `--exclude=foo` is a single arg; `bar+` is a bare positional
    assert _promote_selectors("build", ["--exclude=foo", "bar+"]) == [
        "--exclude=foo",
        "--select",
        "bar+",
    ]
