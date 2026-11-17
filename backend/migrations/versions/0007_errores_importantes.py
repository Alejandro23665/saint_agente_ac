"""Negocios desactivados sin webhook, y barrido de mensajes atascados en 'sending'

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-17 00:30:00+00:00
"""

import re
from collections.abc import Sequence

from alembic import op

from app.core.config import get_settings

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STUCK_FUNCTION = "stuck_sending_messages(interval, integer)"


def app_role() -> str:
    """Igual que en 0001: el usuario de la app, validado porque va dentro del SQL."""
    role = get_settings().app_db_user
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", role):
        raise ValueError(f"APP_DB_USER no válido: {role!r}")
    return role


def resolve_sql(only_active_tenants: bool) -> str:
    tenant_check = (
        """
      AND EXISTS (
          SELECT 1 FROM tenants t
          WHERE t.id = ci.tenant_id AND t.is_active AND t.deleted_at IS NULL
      )"""
        if only_active_tenants
        else ""
    )
    # CREATE OR REPLACE con la misma firma: se conservan los permisos de 0001.
    return f"""
CREATE OR REPLACE FUNCTION resolve_channel_instance(p_public_id text)
RETURNS TABLE (id uuid, tenant_id uuid)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = public, pg_temp AS $$
    SELECT ci.id, ci.tenant_id
    FROM channel_instances ci
    WHERE ci.public_id = p_public_id AND ci.deleted_at IS NULL{tenant_check}
$$;
"""


def upgrade() -> None:
    # 1) El webhook de un negocio desactivado o borrado se rechaza (404), igual que el de
    #    un número borrado: no se guarda el aviso y el bot no contesta.
    op.execute(resolve_sql(only_active_tenants=True))

    # 2) Mensajes que se quedaron en 'sending' (Evolution no respondió a tiempo y nunca
    #    llegó el aviso send.message, o el worker se cayó a mitad). Como los otros
    #    barridos, salta RLS pero solo devuelve id y negocio.
    op.execute(
        """
CREATE FUNCTION stuck_sending_messages(p_older_than interval, p_limit integer)
RETURNS TABLE (id uuid, tenant_id uuid)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = public, pg_temp AS $$
    SELECT m.id, m.tenant_id
    FROM messages m
    WHERE m.status = 'sending'
      AND m.direction = 'outbound'
      AND m.updated_at < now() - p_older_than
    ORDER BY m.updated_at
    LIMIT p_limit
$$;
"""
    )
    op.execute(f"REVOKE ALL ON FUNCTION {STUCK_FUNCTION} FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION {STUCK_FUNCTION} TO {app_role()}")


def downgrade() -> None:
    op.execute(f"DROP FUNCTION IF EXISTS {STUCK_FUNCTION}")
    op.execute(resolve_sql(only_active_tenants=False))
