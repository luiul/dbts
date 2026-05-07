from __future__ import annotations

from typing import Any

import snowflake.connector
from snowflake.connector import SnowflakeConnection

from dbts.config import Target


def connect(target: Target) -> SnowflakeConnection:
    """Open a Snowflake connection using the given dbt target.

    Reuses the OS-level externalbrowser SSO token cache so the browser only
    opens on the first auth in a session.
    """
    return snowflake.connector.connect(
        account=target.account,
        user=target.user,
        role=target.role,
        authenticator=target.authenticator,
        warehouse=target.warehouse,
        client_session_keep_alive=True,
    )


def run_sql(conn: SnowflakeConnection, sql: str) -> list[dict[str, Any]]:
    """Execute a SQL statement and return rows as dicts (column name -> value)."""
    with conn.cursor() as cur:
        cur.execute(sql)
        if cur.description is None:
            return []
        columns = [c[0] for c in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]
