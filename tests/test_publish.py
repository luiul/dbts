from __future__ import annotations

import pytest

from dbts import publish
from dbts.config import ConfigError, Target


def _sandbox(database: str = "ANALYTICS_SANDBOX_LUIS") -> Target:
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


class _FakeConn:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake_snowflake(monkeypatch):
    """Stub out Snowflake I/O — record SQL, never connect."""
    state: dict = {"sql": [], "conn": None}

    def fake_connect(target):
        conn = _FakeConn()
        state["conn"] = conn
        state["target"] = target
        return conn

    def fake_run_sql(conn, sql):
        state["sql"].append(sql)
        return []

    monkeypatch.setattr(publish, "connect", fake_connect)
    monkeypatch.setattr(publish, "run_sql", fake_run_sql)
    return state


@pytest.fixture
def stub_sandbox(monkeypatch):
    target = _sandbox()
    monkeypatch.setattr(publish, "read_profile", lambda profile, name: target)
    monkeypatch.setattr(publish, "default_profile_name", lambda: "p")
    monkeypatch.setattr(publish, "require_exists", lambda: None)
    return target


def test_publish_issues_seven_grants_in_order(fake_snowflake, stub_sandbox):
    rc = publish.publish(yes=True)
    assert rc == 0
    expected = [
        'GRANT USAGE ON DATABASE "ANALYTICS_SANDBOX_LUIS" TO ROLE PUBLIC',
        'GRANT USAGE ON ALL SCHEMAS IN DATABASE "ANALYTICS_SANDBOX_LUIS" TO ROLE PUBLIC',
        'GRANT USAGE ON FUTURE SCHEMAS IN DATABASE "ANALYTICS_SANDBOX_LUIS" TO ROLE PUBLIC',
        'GRANT SELECT ON ALL TABLES IN DATABASE "ANALYTICS_SANDBOX_LUIS" TO ROLE PUBLIC',
        'GRANT SELECT ON FUTURE TABLES IN DATABASE "ANALYTICS_SANDBOX_LUIS" TO ROLE PUBLIC',
        'GRANT SELECT ON ALL VIEWS IN DATABASE "ANALYTICS_SANDBOX_LUIS" TO ROLE PUBLIC',
        'GRANT SELECT ON FUTURE VIEWS IN DATABASE "ANALYTICS_SANDBOX_LUIS" TO ROLE PUBLIC',
    ]
    assert fake_snowflake["sql"] == expected
    assert fake_snowflake["conn"].closed is True


def test_unpublish_issues_revokes_in_reverse_dependency_order(fake_snowflake, stub_sandbox):
    rc = publish.unpublish(yes=True)
    assert rc == 0
    expected = [
        'REVOKE SELECT ON ALL TABLES IN DATABASE "ANALYTICS_SANDBOX_LUIS" FROM ROLE PUBLIC',
        'REVOKE SELECT ON FUTURE TABLES IN DATABASE "ANALYTICS_SANDBOX_LUIS" FROM ROLE PUBLIC',
        'REVOKE SELECT ON ALL VIEWS IN DATABASE "ANALYTICS_SANDBOX_LUIS" FROM ROLE PUBLIC',
        'REVOKE SELECT ON FUTURE VIEWS IN DATABASE "ANALYTICS_SANDBOX_LUIS" FROM ROLE PUBLIC',
        'REVOKE USAGE ON ALL SCHEMAS IN DATABASE "ANALYTICS_SANDBOX_LUIS" FROM ROLE PUBLIC',
        'REVOKE USAGE ON FUTURE SCHEMAS IN DATABASE "ANALYTICS_SANDBOX_LUIS" FROM ROLE PUBLIC',
        'REVOKE USAGE ON DATABASE "ANALYTICS_SANDBOX_LUIS" FROM ROLE PUBLIC',
    ]
    assert fake_snowflake["sql"] == expected


def test_publish_normalises_lowercase_database(fake_snowflake, monkeypatch):
    target = _sandbox(database="analytics_sandbox_luis")
    monkeypatch.setattr(publish, "read_profile", lambda profile, name: target)
    monkeypatch.setattr(publish, "default_profile_name", lambda: "p")
    monkeypatch.setattr(publish, "require_exists", lambda: None)

    publish.publish(yes=True)

    assert all('"ANALYTICS_SANDBOX_LUIS"' in stmt for stmt in fake_snowflake["sql"])


def test_publish_rejects_non_sandbox_target(fake_snowflake, monkeypatch):
    target = _sandbox(database="ANALYTICS_PROD")
    monkeypatch.setattr(publish, "read_profile", lambda profile, name: target)
    monkeypatch.setattr(publish, "default_profile_name", lambda: "p")
    monkeypatch.setattr(publish, "require_exists", lambda: None)

    with pytest.raises(ConfigError, match="does not match the expected pattern"):
        publish.publish(yes=True)
    assert fake_snowflake["sql"] == []


def test_publish_aborts_when_user_says_no(fake_snowflake, stub_sandbox, monkeypatch):
    monkeypatch.setattr(publish, "_confirm", lambda prompt: False)
    rc = publish.publish(yes=False)
    assert rc == 1
    assert fake_snowflake["sql"] == []


def test_publish_skips_confirmation_with_yes(fake_snowflake, stub_sandbox, monkeypatch):
    def boom(prompt):
        raise AssertionError("confirm should not be called when yes=True")

    monkeypatch.setattr(publish, "_confirm", boom)
    rc = publish.publish(yes=True)
    assert rc == 0
    assert len(fake_snowflake["sql"]) == 7
