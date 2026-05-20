from __future__ import annotations

import logging

from dbts.clone import _confirm, _sql_ident, require_exists
from dbts.config import Target, default_profile_name, read_profile, sandbox_user
from dbts.snowflake import connect, run_sql

log = logging.getLogger("dbts.publish")

_GRANT_TEMPLATES: tuple[str, ...] = (
    "GRANT USAGE ON DATABASE {db} TO ROLE PUBLIC",
    "GRANT USAGE ON ALL SCHEMAS IN DATABASE {db} TO ROLE PUBLIC",
    "GRANT USAGE ON FUTURE SCHEMAS IN DATABASE {db} TO ROLE PUBLIC",
    "GRANT SELECT ON ALL TABLES IN DATABASE {db} TO ROLE PUBLIC",
    "GRANT SELECT ON FUTURE TABLES IN DATABASE {db} TO ROLE PUBLIC",
    "GRANT SELECT ON ALL VIEWS IN DATABASE {db} TO ROLE PUBLIC",
    "GRANT SELECT ON FUTURE VIEWS IN DATABASE {db} TO ROLE PUBLIC",
)

# Tear down in reverse: object-level grants first, then schema usage, then DB usage.
_REVOKE_TEMPLATES: tuple[str, ...] = (
    "REVOKE SELECT ON ALL TABLES IN DATABASE {db} FROM ROLE PUBLIC",
    "REVOKE SELECT ON FUTURE TABLES IN DATABASE {db} FROM ROLE PUBLIC",
    "REVOKE SELECT ON ALL VIEWS IN DATABASE {db} FROM ROLE PUBLIC",
    "REVOKE SELECT ON FUTURE VIEWS IN DATABASE {db} FROM ROLE PUBLIC",
    "REVOKE USAGE ON ALL SCHEMAS IN DATABASE {db} FROM ROLE PUBLIC",
    "REVOKE USAGE ON FUTURE SCHEMAS IN DATABASE {db} FROM ROLE PUBLIC",
    "REVOKE USAGE ON DATABASE {db} FROM ROLE PUBLIC",
)


def _resolve_sandbox() -> tuple[Target, str]:
    target = read_profile(default_profile_name(), "sandbox")
    sandbox_user(target)
    return target, target.database.upper()


def publish(yes: bool = False) -> int:
    require_exists()
    target, db = _resolve_sandbox()

    if not yes and not _confirm(f"Grant USAGE+SELECT on {db} (incl. future objects) to ROLE PUBLIC?"):
        log.warning("[yellow]Aborted.[/yellow]")
        return 1

    db_id = _sql_ident(db)
    conn = connect(target)
    try:
        for tmpl in _GRANT_TEMPLATES:
            ddl = tmpl.format(db=db_id)
            log.debug("[dim]%s[/dim]", ddl)
            run_sql(conn, ddl)
    finally:
        conn.close()
    log.info("[green]Published[/green] %s to ROLE PUBLIC", db)
    return 0


def unpublish(yes: bool = False) -> int:
    require_exists()
    target, db = _resolve_sandbox()

    if not yes and not _confirm(f"Revoke ROLE PUBLIC access to {db}?"):
        log.warning("[yellow]Aborted.[/yellow]")
        return 1

    db_id = _sql_ident(db)
    conn = connect(target)
    try:
        for tmpl in _REVOKE_TEMPLATES:
            ddl = tmpl.format(db=db_id)
            log.debug("[dim]%s[/dim]", ddl)
            run_sql(conn, ddl)
    finally:
        conn.close()
    log.info("[green]Unpublished[/green] %s from ROLE PUBLIC", db)
    return 0
