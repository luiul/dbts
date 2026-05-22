from __future__ import annotations

import logging
import os
import re
import time
from typing import Any

# The Snowflake connector probes EC2/Lambda/AWS-identity on import-time of its
# auth path. The probe walks the boto3 credential chain, which surfaces a noisy
# traceback when the user's AWS SSO session is expired. We never run on AWS, so
# disable the probe entirely. Must be set before `import snowflake.connector`.
os.environ.setdefault("SNOWFLAKE_DISABLE_PLATFORM_DETECTION", "true")

import snowflake.connector
from snowflake.connector import SnowflakeConnection
from snowflake.connector.errors import (
    DatabaseError,
    InterfaceError,
    OperationalError,
    ProgrammingError,
)

from dbts.config import Target

log = logging.getLogger("dbts.snowflake")

_RETRY_ATTEMPTS = 3
_RETRY_BACKOFF_S: tuple[float, ...] = (0.5, 1.5)
_TRANSIENT_MESSAGE_RE = re.compile(r"timed?\s*out|timeout|temporary|connection reset|network", re.IGNORECASE)


def log_target_info(target: Target) -> None:
    """Print a one-line dim summary of which Snowflake context we're using.

    Helps the user spot wrong-role/wrong-warehouse problems before they hit
    permission errors mid-command.
    """
    log.info(
        "[dim]target=%s · role=%s · warehouse=%s · account=%s · user=%s[/dim]",
        target.name,
        target.role,
        target.warehouse,
        target.account,
        target.user,
    )


def _is_transient(err: BaseException) -> bool:
    # OperationalError is itself a DatabaseError subclass in snowflake-connector,
    # so check it first before the catch-all misconfig classes.
    if isinstance(err, OperationalError):
        return True
    if isinstance(err, (ProgrammingError, InterfaceError, DatabaseError)):
        return False
    return bool(_TRANSIENT_MESSAGE_RE.search(str(err)))


def connect(target: Target) -> SnowflakeConnection:
    """Open a Snowflake connection using the given dbt target.

    Reuses the OS-level externalbrowser SSO token cache so the browser only
    opens on the first auth in a session. Transient auth/network failures are
    retried up to two times with short backoff; misconfig errors fail fast.
    """
    last_err: BaseException | None = None
    for attempt in range(1, _RETRY_ATTEMPTS + 1):
        try:
            return snowflake.connector.connect(
                account=target.account,
                user=target.user,
                role=target.role,
                authenticator=target.authenticator,
                warehouse=target.warehouse,
                client_session_keep_alive=True,
            )
        except Exception as err:
            last_err = err
            if attempt == _RETRY_ATTEMPTS or not _is_transient(err):
                raise
            delay = _RETRY_BACKOFF_S[attempt - 1]
            log.warning(
                "[yellow]Auth attempt %d/%d failed: %s — retrying in %.1fs[/yellow]",
                attempt,
                _RETRY_ATTEMPTS,
                err,
                delay,
            )
            time.sleep(delay)
    assert last_err is not None
    raise last_err


def run_sql(conn: SnowflakeConnection, sql: str) -> list[dict[str, Any]]:
    """Execute a SQL statement and return rows as dicts (column name -> value)."""
    with conn.cursor() as cur:
        cur.execute(sql)
        if cur.description is None:
            return []
        columns = [c[0] for c in cur.description]
        return [dict(zip(columns, row, strict=True)) for row in cur.fetchall()]
