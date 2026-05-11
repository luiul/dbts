from __future__ import annotations

from dbts.clone import _sql_ident, _sql_quote


def test_sql_ident_wraps_in_double_quotes():
    assert _sql_ident("DB") == '"DB"'


def test_sql_ident_doubles_embedded_double_quotes():
    assert _sql_ident('weird"name') == '"weird""name"'


def test_sql_ident_does_not_touch_single_quotes():
    assert _sql_ident("o'connor") == '"o\'connor"'


def test_sql_quote_doubles_single_quotes():
    assert _sql_quote("it's") == "it''s"


def test_sql_quote_does_not_touch_double_quotes():
    assert _sql_quote('say "hi"') == 'say "hi"'


def test_sql_quote_passthrough_for_plain_string():
    assert _sql_quote("plain string with no quotes") == "plain string with no quotes"
