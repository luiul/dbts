from __future__ import annotations

import pytest
from snowflake.connector.errors import OperationalError, ProgrammingError

from dbts import snowflake as sf
from dbts.config import Target


def _target() -> Target:
    return Target(
        name="sandbox",
        type="snowflake",
        account="acct",
        user="u",
        role="r",
        authenticator="externalbrowser",
        warehouse="w",
        database="ANALYTICS_SANDBOX_LUIS",
        schema="s",
    )


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(sf.time, "sleep", lambda _s: None)


def test_connect_succeeds_after_two_transient_errors(monkeypatch):
    calls = {"n": 0}

    def fake_connect(**kwargs):
        calls["n"] += 1
        if calls["n"] < 3:
            raise OperationalError("network connection reset")
        return "CONN"

    monkeypatch.setattr(sf.snowflake.connector, "connect", fake_connect)

    assert sf.connect(_target()) == "CONN"
    assert calls["n"] == 3


def test_connect_fast_fails_on_programming_error(monkeypatch):
    calls = {"n": 0}

    def fake_connect(**kwargs):
        calls["n"] += 1
        raise ProgrammingError("invalid role")

    monkeypatch.setattr(sf.snowflake.connector, "connect", fake_connect)

    with pytest.raises(ProgrammingError):
        sf.connect(_target())
    assert calls["n"] == 1


def test_connect_raises_after_max_attempts(monkeypatch):
    calls = {"n": 0}

    def fake_connect(**kwargs):
        calls["n"] += 1
        raise OperationalError("timeout")

    monkeypatch.setattr(sf.snowflake.connector, "connect", fake_connect)

    with pytest.raises(OperationalError):
        sf.connect(_target())
    assert calls["n"] == sf._RETRY_ATTEMPTS
