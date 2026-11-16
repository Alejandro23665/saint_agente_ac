"""Tabla tenant_invitations: invitar personas a un negocio con un enlace de un solo uso

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-16 02:39:19+00:00
"""

import re
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.config import get_settings

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FUNCTION = "open_invitation(text)"


def app_role() -> str:
    """Igual que en 0001: el usuario de la app, validado porque va dentro del SQL."""
    role = get_settings().app_db_user
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", role):
        raise ValueError(f"APP_DB_USER no válido: {role!r}")
    return role


def upgrade() -> None:
    op.create_table(
        "tenant_invitations",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("email", postgresql.CITEXT(), nullable=False),
        # El tipo membership_role ya existe (0001): no se vuelve a crear.
        sa.Column(
            "role", postgresql.ENUM(name="membership_role", create_type=False), nullable=False
        ),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("invited_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["accepted_by_user_id"],
            ["users.id"],
            name=op.f("fk_tenant_invitations_accepted_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["invited_by_user_id"],
            ["users.id"],
            name=op.f("fk_tenant_invitations_invited_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_tenant_invitations_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tenant_invitations")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_tenant_invitations_token_hash")),
    )
    op.create_index(
        "uq_tenant_invitations_open",
        "tenant_invitations",
        ["tenant_id", "email"],
        unique=True,
        postgresql_where=sa.text("accepted_at IS NULL AND revoked_at IS NULL"),
    )

    role = app_role()
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON tenant_invitations TO {role}")
    # Cada negocio ve solo sus invitaciones, igual que el resto de sus datos.
    op.execute("ALTER TABLE tenant_invitations ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE tenant_invitations FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON tenant_invitations "
        "USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant())"
    )
    # Quien abre el enlace todavía no tiene sesión ni negocio activo. Como
    # resolve_channel_instance (0001), esta función salta RLS, pero solo devuelve el id y
    # el negocio de UNA invitación abierta, y solo a quien ya tiene la huella del enlace.
    op.execute(
        """
CREATE FUNCTION open_invitation(p_token_hash text)
RETURNS TABLE (id uuid, tenant_id uuid)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = public, pg_temp AS $$
    SELECT i.id, i.tenant_id
    FROM tenant_invitations i
    JOIN tenants t ON t.id = i.tenant_id
    WHERE i.token_hash = p_token_hash
      AND i.accepted_at IS NULL
      AND i.revoked_at IS NULL
      AND i.expires_at > now()
      AND t.is_active AND t.deleted_at IS NULL
$$;
"""
    )
    op.execute(f"REVOKE ALL ON FUNCTION {FUNCTION} FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION {FUNCTION} TO {role}")


def downgrade() -> None:
    op.execute(f"DROP FUNCTION IF EXISTS {FUNCTION}")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON tenant_invitations")
    op.drop_index(
        "uq_tenant_invitations_open",
        table_name="tenant_invitations",
        postgresql_where=sa.text("accepted_at IS NULL AND revoked_at IS NULL"),
    )
    op.drop_table("tenant_invitations")
