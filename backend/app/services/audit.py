"""Registro de auditoría: quién hizo qué en el panel (tabla audit_logs).

Solo se agregan filas: la base no deja a la app editarlas ni borrarlas (ver 0001).
"""

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog


def record(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    actor_user_id: uuid.UUID | None,
    action: str,
    entity_type: str,
    entity_id: uuid.UUID | None = None,
    diff: dict[str, Any] | None = None,
    ip: str | None = None,
) -> None:
    """Agrega la fila a la transacción en curso: se guarda solo si la acción se guarda."""
    session.add(
        AuditLog(
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            diff=diff,
            ip=ip,
        )
    )
