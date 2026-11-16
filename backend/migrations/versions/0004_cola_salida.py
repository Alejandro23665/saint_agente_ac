"""Función para que el worker encuentre mensajes salientes que quedaron en cola

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-16 01:00:00+00:00
"""

import re
from collections.abc import Sequence

from alembic import op

from app.core.config import get_settings

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FUNCTION = "queued_outbound_messages(interval, integer)"


def app_role() -> str:
    """Igual que en 0001: el usuario de la app, validado porque va dentro del SQL."""
    role = get_settings().app_db_user
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", role):
        raise ValueError(f"APP_DB_USER no válido: {role!r}")
    return role


def upgrade() -> None:
    # Como pending_webhook_events (0002): el barrido mira todos los negocios, así que
    # salta RLS, pero solo devuelve id y negocio, nunca el texto del mensaje.
    op.execute(
        """
CREATE FUNCTION queued_outbound_messages(p_older_than interval, p_limit integer)
RETURNS TABLE (id uuid, tenant_id uuid)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = public, pg_temp AS $$
    SELECT m.id, m.tenant_id
    FROM messages m
    WHERE m.status = 'queued'
      AND m.direction = 'outbound'
      AND m.created_at < now() - p_older_than
    ORDER BY m.created_at
    LIMIT p_limit
$$;
"""
    )
    op.execute(f"REVOKE ALL ON FUNCTION {FUNCTION} FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION {FUNCTION} TO {app_role()}")


def downgrade() -> None:
    op.execute(f"DROP FUNCTION IF EXISTS {FUNCTION}")
