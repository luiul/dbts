from __future__ import annotations

import pytest

from dbts.config import ConfigError, _render_env_vars


def test_default_used_when_env_unset(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("DBTS_TEST_VAR", raising=False)
    assert _render_env_vars("{{ env_var('DBTS_TEST_VAR', 'fallback') }}") == "fallback"


def test_env_value_overrides_default(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DBTS_TEST_VAR", "from-env")
    assert _render_env_vars("{{ env_var('DBTS_TEST_VAR', 'fallback') }}") == "from-env"


def test_missing_env_without_default_raises(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("DBTS_TEST_VAR", raising=False)
    with pytest.raises(ConfigError, match="DBTS_TEST_VAR"):
        _render_env_vars("{{ env_var('DBTS_TEST_VAR') }}")


def test_embedded_in_larger_string(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("warehouse", raising=False)
    assert _render_env_vars("tardis_{{ env_var('warehouse', 'snowflake') }}") == "tardis_snowflake"


def test_no_jinja_in_string_returns_as_is():
    assert _render_env_vars("plain_value") == "plain_value"


def test_double_quotes_in_jinja_args(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("DBTS_TEST_VAR", raising=False)
    assert _render_env_vars('{{ env_var("DBTS_TEST_VAR", "ok") }}') == "ok"
