from __future__ import annotations

import pytest

from dbts.config import ConfigError, Target, sandbox_user


def _target(database: str) -> Target:
    return Target(
        name="sandbox",
        type="snowflake",
        account="acct",
        user="u",
        role="r",
        authenticator="externalbrowser",
        warehouse="w",
        database=database,
        schema="s",
    )


def test_extracts_user_segment():
    assert sandbox_user(_target("SCM_ANALYTICS_SANDBOX_LUIS_ACEITUNO")) == "LUIS_ACEITUNO"


def test_lowercase_database_normalized():
    assert sandbox_user(_target("scm_analytics_sandbox_luis")) == "LUIS"


def test_short_prefix_pattern_works():
    assert sandbox_user(_target("X_SANDBOX_Y")) == "Y"


def test_invalid_pattern_raises():
    with pytest.raises(ConfigError, match="does not match the expected pattern"):
        sandbox_user(_target("NOT_VALID"))


def test_database_must_have_sandbox_segment():
    with pytest.raises(ConfigError):
        sandbox_user(_target("ANALYTICS_PROD_LUIS"))
