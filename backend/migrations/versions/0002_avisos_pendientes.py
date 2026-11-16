"""Función para que el worker encuentre avisos pendientes de todos los negocios

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-15 21:40:00+00:00
"""

import re
from collections.abc import Sequence

from alembic import op

from app.core.config import get_settings

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FUNCTION = "pending_webhook_events(text[], interval, integer, integer)"


def app_role() -> str:
    """Igual que en 0001: el usuario de la app, validado porque va dentro del SQL."""
    role = get_settings().app_db_user
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", role):
        raise ValueError(f"APP_DB_USER no válido: {role!r}")
    return role


def upgrade() -> None:
    # El barrido del worker revisa TODOS los negocios a la vez, así que RLS no le dejaría
    # ver nada. Igual que resolve_channel_instance, esta función corre con los permisos
    # de su dueño, pero solo devuelve id y negocio de avisos pendientes, nada del contenido.
    op.execute(
        """
CREATE FUNCTION pending_webhook_events(
    p_event_types text[], p_older_than interval, p_max_attempts integer, p_limit integer
)
RETURNS TABLE (id uuid, tenant_id uuid)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = public, pg_temp AS $$
    SELECT we.id, we.tenant_id
    FROM webhook_events we
    WHERE we.status = 'pending'
      AND we.event_type = ANY (p_event_types)
      AND we.received_at < now() - p_older_than
      AND we.attempts < p_max_attempts
    ORDER BY we.received_at
    LIMIT p_limit
$$;
"""
    )
    op.execute(f"REVOKE ALL ON FUNCTION {FUNCTION} FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION {FUNCTION} TO {app_role()}")


def downgrade() -> None:
    op.execute(f"DROP FUNCTION IF EXISTS {FUNCTION}")
