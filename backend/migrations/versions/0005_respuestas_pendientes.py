"""Función para que el worker encuentre conversaciones sin contestar

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-16 02:00:00+00:00
"""

import re
from collections.abc import Sequence

from alembic import op

from app.core.config import get_settings

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FUNCTION = "conversations_awaiting_reply(interval, interval, integer)"


def app_role() -> str:
    """Igual que en 0001: el usuario de la app, validado porque va dentro del SQL."""
    role = get_settings().app_db_user
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", role):
        raise ValueError(f"APP_DB_USER no válido: {role!r}")
    return role


def upgrade() -> None:
    # Los mensajes que llegaron antes de que existiera el bot no se contestan: se dan
    # por atendidos. Si no, al encender el bot respondería a conversaciones viejas.
    op.execute(
        "UPDATE messages SET processed_at = now() "
        "WHERE direction = 'inbound' AND processed_at IS NULL"
    )
    # Como queued_outbound_messages (0004): mira todos los negocios, así que salta RLS,
    # pero solo devuelve ids, nunca el texto de los mensajes.
    op.execute(
        """
CREATE FUNCTION conversations_awaiting_reply(
    p_older_than interval, p_max_age interval, p_limit integer
)
RETURNS TABLE (conversation_id uuid, tenant_id uuid)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = public, pg_temp AS $$
    SELECT m.conversation_id, m.tenant_id
    FROM messages m
    JOIN conversations c ON c.id = m.conversation_id
    WHERE m.direction = 'inbound'
      AND m.processed_at IS NULL
      AND m.created_at < now() - p_older_than
      AND m.created_at > now() - p_max_age
      AND c.status = 'open'
      AND c.mode = 'bot'
    GROUP BY m.conversation_id, m.tenant_id
    ORDER BY min(m.created_at)
    LIMIT p_limit
$$;
"""
    )
    op.execute(f"REVOKE ALL ON FUNCTION {FUNCTION} FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION {FUNCTION} TO {app_role()}")


def downgrade() -> None:
    op.execute(f"DROP FUNCTION IF EXISTS {FUNCTION}")
