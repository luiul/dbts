from __future__ import annotations

import pytest

from dbts.cli import _hint_for


@pytest.mark.parametrize(
    "msg,expected_substring",
    [
        ("could not determine dbt profile name. Set $DBTS_PROFILE", "set $DBTS_PROFILE"),
        ("could not find dbt_project.yml walking up from /tmp", "inside a dbt project"),
        ("sandbox database FOO_SANDBOX_BAR does not exist. Run ...", "dbts up --from"),
        ("sandbox target's database 'FOO' does not match the expected pattern", "_SANDBOX_<USER>"),
        ("profile 'tardis_snowflake' not found in /Users/x/.dbt/profiles.yml", "check the profile name"),
        ("target 'sandbox' not found under profile 'foo'. Available targets: dev", "missing target"),
        ("dbt not found on PATH. Activate the venv where dbt-core is installed.", "dbt-snowflake"),
    ],
)
def test_hint_matches(msg: str, expected_substring: str):
    hint = _hint_for(msg)
    assert hint is not None
    assert expected_substring in hint


def test_hint_returns_none_for_unknown_message():
    assert _hint_for("some unrelated error nobody anticipated") is None


def test_hint_lookup_is_case_insensitive():
    assert _hint_for("DBT NOT FOUND ON PATH.") is not None
