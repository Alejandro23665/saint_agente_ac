"""Esquema inicial de Saint: 19 tablas, índices, tipos ENUM y seguridad por negocio (RLS)

Revision ID: 0001
Revises:
Create Date: 2026-11-15 16:41:02.288042+00:00
"""

import re
from collections.abc import Sequence

import pgvector.sqlalchemy
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.config import get_settings

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Tipos ENUM de PostgreSQL. Se crean una sola vez antes de las tablas, porque algunos
# (channel, conversation_mode) los usan varias tablas.
ENUMS: dict[str, tuple[str, ...]] = {
    "billing_mode": ("fixed", "usage"),
    "agent_kind": ("router", "specialist"),
    "catalog_item_type": ("product", "service"),
    "channel": ("whatsapp", "instagram"),
    "channel_provider": ("evolution", "meta_cloud"),
    "instance_status": ("created", "qr_pending", "open", "connecting", "close", "banned"),
    "data_source_engine": ("postgres", "mysql", "sqlserver", "rest_api", "google_sheets"),
    "connection_status": ("pending", "ok", "error"),
    "knowledge_source_type": ("file", "url", "text"),
    "processing_status": ("pending", "processing", "ready", "failed"),
    "membership_role": ("owner", "admin", "agent"),
    "conversation_status": ("open", "closed"),
    "conversation_mode": ("bot", "human", "paused"),
    "webhook_event_status": ("pending", "processed", "failed", "ignored"),
    "handoff_reason": (
        "panel",
        "phone",
        "agent_tool",
        "customer_request",
        "low_confidence",
        "errors",
        "returned",
    ),
    "llm_run_status": ("ok", "error", "discarded"),
    "message_direction": ("inbound", "outbound"),
    "sender_type": ("contact", "bot", "human_panel", "human_phone", "system"),
    "content_type": ("text", "audio", "image", "video", "document", "sticker", "location"),
    "transcription_status": ("none", "pending", "done", "failed"),
    "message_status": ("received", "queued", "sending", "sent", "delivered", "read", "failed"),
}

EXTENSIONS = ("vector", "citext", "pg_trgm")

# Tablas con tenant_id: cada fila solo la ve el negocio dueño.
TENANT_TABLES = (
    "channel_instances",
    "contacts",
    "conversations",
    "messages",
    "webhook_events",
    "handoff_events",
    "agents",
    "llm_runs",
    "knowledge_sources",
    "knowledge_chunks",
    "catalog_items",
    "external_data_sources",
    "usage_daily",
    "audit_logs",
)
# Casos especiales con su propia política: tenants, tenant_memberships, agent_versions.
# Sin RLS: users y refresh_tokens (un usuario puede pertenecer a varios negocios y hay
# que encontrarlo al iniciar sesión, antes de saber en qué negocio va a trabajar).
SPECIAL_RLS_TABLES = ("tenants", "tenant_memberships", "agent_versions")

# asyncpg ejecuta una sentencia por llamada, por eso van separadas.
FUNCTIONS_SQL: tuple[str, ...] = (
    """
-- Negocio activo. Si la aplicación olvidó definirlo, la consulta falla en vez de
-- devolver datos de otro negocio o una lista vacía silenciosa.
CREATE FUNCTION app_current_tenant() RETURNS uuid
LANGUAGE plpgsql STABLE AS $$
DECLARE
    v text := current_setting('app.tenant_id', true);
BEGIN
    IF v IS NULL OR v = '' THEN
        RAISE EXCEPTION 'app.tenant_id no está definido: abre la sesión con tenant_session()'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN v::uuid;
END
$$;
""",
    """
-- Igual, pero devuelve NULL si no hay negocio activo (pantalla de elegir negocio).
CREATE FUNCTION app_current_tenant_optional() RETURNS uuid
LANGUAGE sql STABLE AS $$
    SELECT nullif(current_setting('app.tenant_id', true), '')::uuid
$$;
""",
    """
-- Usuario del panel que hace la petición (NULL en webhooks y workers).
CREATE FUNCTION app_current_user() RETURNS uuid
LANGUAGE sql STABLE AS $$
    SELECT nullif(current_setting('app.user_id', true), '')::uuid
$$;
""",
    """
-- El webhook llega con /webhooks/evolution/{public_id} y todavía no sabe de qué negocio
-- es. Esta función salta RLS (SECURITY DEFINER: corre con los permisos de su dueño) pero
-- solo devuelve el id y el negocio de UNA instancia activa, nada más.
CREATE FUNCTION resolve_channel_instance(p_public_id text)
RETURNS TABLE (id uuid, tenant_id uuid)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = public, pg_temp AS $$
    SELECT ci.id, ci.tenant_id
    FROM channel_instances ci
    WHERE ci.public_id = p_public_id AND ci.deleted_at IS NULL
$$;
""",
    """
REVOKE ALL ON FUNCTION resolve_channel_instance(text) FROM PUBLIC;
""",
)

FUNCTION_NAMES = (
    "resolve_channel_instance(text)",
    "app_current_user()",
    "app_current_tenant_optional()",
    "app_current_tenant()",
)


def app_role() -> str:
    """Usuario con el que se conecta la aplicación (APP_DB_USER del .env, por defecto saint_app)."""
    role = get_settings().app_db_user
    # El nombre va dentro del SQL; solo aceptamos letras, números y guion bajo.
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", role):
        raise ValueError(f"APP_DB_USER no válido: {role!r}")
    return role


def enable_rls(table: str, using: str, check: str | None = None) -> None:
    # ENABLE activa RLS; FORCE hace que también aplique al dueño de la tabla.
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {table} USING ({using}) WITH CHECK ({check or using})"
    )


def upgrade_rls() -> None:
    role = app_role()

    # Si el rol no existe (por ejemplo, fuera de Docker), se crea sin permiso de login;
    # el administrador le pone contraseña después. Nunca puede saltarse RLS.
    op.execute(
        f"""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                CREATE ROLE {role} NOLOGIN NOSUPERUSER NOBYPASSRLS;
            END IF;
        END $$;
        """
    )

    for statement in FUNCTIONS_SQL:
        op.execute(statement)
    op.execute(f"GRANT EXECUTE ON FUNCTION resolve_channel_instance(text) TO {role}")

    # Permisos de datos: leer y escribir filas, nunca cambiar la estructura.
    op.execute(f"GRANT USAGE ON SCHEMA public TO {role}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {role}")
    op.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {role}")
    # Tablas que creen migraciones futuras reciben los mismos permisos automáticamente.
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {role}"
    )
    # La app no toca el control de versiones de Alembic.
    op.execute(f"REVOKE ALL ON alembic_version FROM {role}")
    # La auditoría solo se escribe una vez: la app no puede editarla ni borrarla.
    op.execute(f"REVOKE UPDATE, DELETE ON audit_logs FROM {role}")

    for table in TENANT_TABLES:
        enable_rls(table, "tenant_id = app_current_tenant()")

    # tenants: se ve el negocio activo y, en la pantalla de elegir negocio, todos aquellos
    # de los que el usuario es miembro. Solo se puede escribir el negocio activo.
    enable_rls(
        "tenants",
        using="id = app_current_tenant_optional() OR id IN ("
        "SELECT m.tenant_id FROM tenant_memberships m WHERE m.user_id = app_current_user())",
        check="id = app_current_tenant_optional()",
    )
    # tenant_memberships: las del negocio activo y las propias del usuario.
    enable_rls(
        "tenant_memberships",
        using="tenant_id = app_current_tenant_optional() OR user_id = app_current_user()",
        check="tenant_id = app_current_tenant()",
    )
    # agent_versions no tiene tenant_id: se permite si su agente es visible (y agents ya
    # está filtrado por RLS).
    enable_rls(
        "agent_versions",
        "EXISTS (SELECT 1 FROM agents a WHERE a.id = agent_versions.agent_id)",
    )


def downgrade_rls() -> None:
    role = app_role()
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM {role}"
    )
    # Las políticas usan las funciones: se borran primero.
    for table in (*TENANT_TABLES, *SPECIAL_RLS_TABLES):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
    for fn in FUNCTION_NAMES:
        op.execute(f"DROP FUNCTION IF EXISTS {fn}")
    op.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {role}")
    op.execute(f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {role}")
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {role}")
    # El rol no se borra: lo crea Docker y puede usarse en otras bases de datos.


def upgrade() -> None:
    # 1. Extensiones: vector (embeddings), citext (correo sin distinguir mayúsculas)
    #    y pg_trgm (búsqueda aproximada por nombre).
    for ext in EXTENSIONS:
        op.execute(f"CREATE EXTENSION IF NOT EXISTS {ext}")

    # 2. Tipos ENUM.
    bind = op.get_bind()
    for name, values in ENUMS.items():
        postgresql.ENUM(*values, name=name).create(bind, checkfirst=True)

    # 3. Tablas e índices (generado por Alembic a partir de los modelos y revisado).
    op.create_table(
        "tenants",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("country_code", sa.CHAR(length=2), nullable=False),
        sa.Column("timezone", sa.Text(), server_default="America/Caracas", nullable=False),
        sa.Column(
            "billing_mode",
            postgresql.ENUM(name="billing_mode", create_type=False),
            server_default="usage",
            nullable=False,
        ),
        sa.Column("plan", sa.Text(), nullable=True),
        sa.Column(
            "business_hours",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column(
            "settings", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tenants")),
        sa.UniqueConstraint("slug", name=op.f("uq_tenants_slug")),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("email", postgresql.CITEXT(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("full_name", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("is_platform_admin", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
    )
    op.create_table(
        "agents",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("kind", postgresql.ENUM(name="agent_kind", create_type=False), nullable=False),
        sa.Column("system_prompt", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), server_default="claude-haiku-5-5", nullable=False),
        sa.Column(
            "temperature", sa.Numeric(precision=3, scale=2), server_default="0.30", nullable=False
        ),
        sa.Column(
            "tools", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False
        ),
        sa.Column(
            "handoff_rules",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("current_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("temperature BETWEEN 0 AND 1", name=op.f("ck_agents_temperature_range")),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_agents_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agents")),
        sa.UniqueConstraint("tenant_id", "key", name=op.f("uq_agents_tenant_id_key")),
    )
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("entity_type", sa.Text(), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=True),
        sa.Column("diff", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("ip", postgresql.INET(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name=op.f("fk_audit_logs_actor_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_audit_logs_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_logs")),
    )
    op.create_index(
        "ix_audit_logs_entity_type_entity_id",
        "audit_logs",
        ["entity_type", "entity_id"],
        unique=False,
    )
    op.create_index(
        "ix_audit_logs_tenant_id_created_at",
        "audit_logs",
        ["tenant_id", "created_at"],
        unique=False,
    )
    op.create_table(
        "catalog_items",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("sku", sa.Text(), nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "item_type",
            postgresql.ENUM(name="catalog_item_type", create_type=False),
            server_default="product",
            nullable=False,
        ),
        sa.Column("price", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("currency", sa.CHAR(length=3), server_default="USD", nullable=False),
        sa.Column("stock", sa.Integer(), nullable=True),
        sa.Column(
            "attributes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("embedding", pgvector.sqlalchemy.vector.VECTOR(dim=1024), nullable=True),
        sa.Column("embedding_model", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("price >= 0", name=op.f("ck_catalog_items_price_not_negative")),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_catalog_items_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_catalog_items")),
        sa.UniqueConstraint("tenant_id", "sku", name=op.f("uq_catalog_items_tenant_id_sku")),
    )
    op.create_index(
        "ix_catalog_items_embedding_hnsw",
        "catalog_items",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.create_index(
        "ix_catalog_items_name_trgm",
        "catalog_items",
        ["name"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"name": "gin_trgm_ops"},
    )
    op.create_index(
        "ix_catalog_items_tenant_id_is_active",
        "catalog_items",
        ["tenant_id", "is_active"],
        unique=False,
    )
    op.create_table(
        "channel_instances",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("public_id", sa.Text(), nullable=False),
        sa.Column("channel", postgresql.ENUM(name="channel", create_type=False), nullable=False),
        sa.Column(
            "provider",
            postgresql.ENUM(name="channel_provider", create_type=False),
            server_default="evolution",
            nullable=False,
        ),
        sa.Column("evolution_instance_name", sa.Text(), nullable=True),
        sa.Column("display_identifier", sa.Text(), nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM(name="instance_status", create_type=False),
            server_default="created",
            nullable=False,
        ),
        sa.Column("instance_token_encrypted", sa.LargeBinary(), nullable=True),
        sa.Column("webhook_secret_hash", sa.Text(), nullable=True),
        sa.Column("groups_enabled", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("debounce_seconds", sa.Integer(), server_default="15", nullable=False),
        sa.Column(
            "settings", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column("last_connected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_event_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "debounce_seconds BETWEEN 0 AND 60", name=op.f("ck_channel_instances_debounce_range")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_channel_instances_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_channel_instances")),
        sa.UniqueConstraint(
            "evolution_instance_name", name=op.f("uq_channel_instances_evolution_instance_name")
        ),
        sa.UniqueConstraint("public_id", name=op.f("uq_channel_instances_public_id")),
    )
    op.create_index(
        "ix_channel_instances_tenant_id_status",
        "channel_instances",
        ["tenant_id", "status"],
        unique=False,
    )
    op.create_table(
        "contacts",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("channel", postgresql.ENUM(name="channel", create_type=False), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("phone_e164", sa.Text(), nullable=True),
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.Column("is_group", sa.Boolean(), server_default="false", nullable=False),
        sa.Column(
            "attributes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("blocked", sa.Boolean(), server_default="false", nullable=False),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_contacts_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_contacts")),
        sa.UniqueConstraint(
            "tenant_id",
            "channel",
            "external_id",
            name=op.f("uq_contacts_tenant_id_channel_external_id"),
        ),
    )
    op.create_index(
        "ix_contacts_display_name_trgm",
        "contacts",
        ["display_name"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"display_name": "gin_trgm_ops"},
    )
    op.create_index(
        "ix_contacts_tenant_id_phone_e164", "contacts", ["tenant_id", "phone_e164"], unique=False
    )
    op.create_table(
        "external_data_sources",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "engine",
            postgresql.ENUM(name="data_source_engine", create_type=False),
            nullable=False,
        ),
        sa.Column("connection_encrypted", sa.LargeBinary(), nullable=False),
        sa.Column(
            "allowed_queries",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column(
            "status",
            postgresql.ENUM(name="connection_status", create_type=False),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_external_data_sources_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_external_data_sources")),
    )
    op.create_table(
        "knowledge_sources",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column(
            "source_type",
            postgresql.ENUM(name="knowledge_source_type", create_type=False),
            nullable=False,
        ),
        sa.Column("file_url", sa.Text(), nullable=True),
        sa.Column("checksum", sa.Text(), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(name="processing_status", create_type=False),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("chunk_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_knowledge_sources_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_knowledge_sources_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_knowledge_sources")),
        sa.UniqueConstraint(
            "tenant_id", "checksum", name=op.f("uq_knowledge_sources_tenant_id_checksum")
        ),
    )
    op.create_index(
        "ix_knowledge_sources_tenant_id_status",
        "knowledge_sources",
        ["tenant_id", "status"],
        unique=False,
    )
    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("ip", postgresql.INET(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_refresh_tokens_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_refresh_tokens")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_refresh_tokens_token_hash")),
    )
    op.create_index(
        "ix_refresh_tokens_user_id_expires_at",
        "refresh_tokens",
        ["user_id", "expires_at"],
        unique=False,
    )
    op.create_table(
        "tenant_memberships",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "role", postgresql.ENUM(name="membership_role", create_type=False), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_tenant_memberships_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_tenant_memberships_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tenant_memberships")),
        sa.UniqueConstraint(
            "tenant_id", "user_id", name=op.f("uq_tenant_memberships_tenant_id_user_id")
        ),
    )
    op.create_index(
        "ix_tenant_memberships_user_id", "tenant_memberships", ["user_id"], unique=False
    )
    op.create_table(
        "usage_daily",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("conversations", sa.Integer(), server_default="0", nullable=False),
        sa.Column("inbound_messages", sa.Integer(), server_default="0", nullable=False),
        sa.Column("outbound_messages", sa.Integer(), server_default="0", nullable=False),
        sa.Column("audio_seconds", sa.Integer(), server_default="0", nullable=False),
        sa.Column("llm_input_tokens", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("llm_output_tokens", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_usage_daily_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_usage_daily")),
        sa.UniqueConstraint("tenant_id", "day", name=op.f("uq_usage_daily_tenant_id_day")),
    )
    op.create_table(
        "agent_versions",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("system_prompt", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("temperature", sa.Numeric(precision=3, scale=2), nullable=False),
        sa.Column(
            "tools", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False
        ),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["agent_id"],
            ["agents.id"],
            name=op.f("fk_agent_versions_agent_id_agents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_agent_versions_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_versions")),
        sa.UniqueConstraint("agent_id", "version", name=op.f("uq_agent_versions_agent_id_version")),
    )
    op.create_table(
        "conversations",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("channel_instance_id", sa.Uuid(), nullable=False),
        sa.Column("contact_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(name="conversation_status", create_type=False),
            server_default="open",
            nullable=False,
        ),
        sa.Column(
            "mode",
            postgresql.ENUM(name="conversation_mode", create_type=False),
            server_default="bot",
            nullable=False,
        ),
        sa.Column("assigned_user_id", sa.Uuid(), nullable=True),
        sa.Column("active_agent_id", sa.Uuid(), nullable=True),
        sa.Column("needs_followup", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("summary_message_id", sa.Uuid(), nullable=True),
        sa.Column("last_inbound_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_outbound_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["active_agent_id"],
            ["agents.id"],
            name=op.f("fk_conversations_active_agent_id_agents"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["assigned_user_id"],
            ["users.id"],
            name=op.f("fk_conversations_assigned_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["channel_instance_id"],
            ["channel_instances.id"],
            name=op.f("fk_conversations_channel_instance_id_channel_instances"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["contact_id"],
            ["contacts.id"],
            name=op.f("fk_conversations_contact_id_contacts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_conversations_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conversations")),
    )
    op.create_index("ix_conversations_contact_id", "conversations", ["contact_id"], unique=False)
    op.create_index(
        "ix_conversations_followup",
        "conversations",
        ["tenant_id"],
        unique=False,
        postgresql_where=sa.text("needs_followup"),
    )
    op.create_index(
        "ix_conversations_tenant_id_status_last_message_at",
        "conversations",
        ["tenant_id", "status", sa.literal_column("last_message_at DESC")],
        unique=False,
    )
    op.create_index(
        "uq_conversations_open_per_contact",
        "conversations",
        ["channel_instance_id", "contact_id"],
        unique=True,
        postgresql_where=sa.text("status = 'open'"),
    )
    op.create_table(
        "knowledge_chunks",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=True),
        sa.Column("embedding", pgvector.sqlalchemy.vector.VECTOR(dim=1024), nullable=True),
        sa.Column("embedding_model", sa.Text(), nullable=True),
        sa.Column(
            "tsv",
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('spanish', content)", persisted=True),
            nullable=False,
        ),
        sa.Column(
            "metadata", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["knowledge_sources.id"],
            name=op.f("fk_knowledge_chunks_source_id_knowledge_sources"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_knowledge_chunks_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_knowledge_chunks")),
        sa.UniqueConstraint(
            "source_id", "chunk_index", name=op.f("uq_knowledge_chunks_source_id_chunk_index")
        ),
    )
    op.create_index(
        "ix_knowledge_chunks_embedding_hnsw",
        "knowledge_chunks",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.create_index(
        "ix_knowledge_chunks_tenant_id", "knowledge_chunks", ["tenant_id"], unique=False
    )
    op.create_index(
        "ix_knowledge_chunks_tsv", "knowledge_chunks", ["tsv"], unique=False, postgresql_using="gin"
    )
    op.create_table(
        "webhook_events",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("channel_instance_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(name="webhook_event_status", create_type=False),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["channel_instance_id"],
            ["channel_instances.id"],
            name=op.f("fk_webhook_events_channel_instance_id_channel_instances"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_webhook_events_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_webhook_events")),
        sa.UniqueConstraint(
            "channel_instance_id",
            "event_type",
            "external_id",
            name=op.f("uq_webhook_events_channel_instance_id_event_type_external_id"),
        ),
    )
    op.create_index(
        "ix_webhook_events_received_at", "webhook_events", ["received_at"], unique=False
    )
    op.create_index(
        "ix_webhook_events_status_received_at",
        "webhook_events",
        ["status", "received_at"],
        unique=False,
    )
    op.create_table(
        "handoff_events",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column(
            "from_mode",
            postgresql.ENUM(name="conversation_mode", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "to_mode", postgresql.ENUM(name="conversation_mode", create_type=False), nullable=False
        ),
        sa.Column(
            "reason",
            postgresql.ENUM(name="handoff_reason", create_type=False),
            nullable=False,
        ),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name=op.f("fk_handoff_events_actor_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_handoff_events_conversation_id_conversations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_handoff_events_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_handoff_events")),
    )
    op.create_index(
        "ix_handoff_events_conversation_id_created_at",
        "handoff_events",
        ["conversation_id", "created_at"],
        unique=False,
    )
    op.create_table(
        "llm_runs",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
        sa.Column("agent_id", sa.Uuid(), nullable=True),
        sa.Column("agent_version", sa.Integer(), nullable=True),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("output_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("cached_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column(
            "tool_calls",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column("router_output", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "status", postgresql.ENUM(name="llm_run_status", create_type=False), nullable=False
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["agent_id"],
            ["agents.id"],
            name=op.f("fk_llm_runs_agent_id_agents"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_llm_runs_conversation_id_conversations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_llm_runs_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_llm_runs")),
    )
    op.create_index("ix_llm_runs_conversation_id", "llm_runs", ["conversation_id"], unique=False)
    op.create_index(
        "ix_llm_runs_tenant_id_created_at", "llm_runs", ["tenant_id", "created_at"], unique=False
    )
    op.create_table(
        "messages",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("channel_instance_id", sa.Uuid(), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=True),
        sa.Column(
            "direction",
            postgresql.ENUM(name="message_direction", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "sender_type",
            postgresql.ENUM(name="sender_type", create_type=False),
            nullable=False,
        ),
        sa.Column("sender_user_id", sa.Uuid(), nullable=True),
        sa.Column("group_participant_id", sa.Text(), nullable=True),
        sa.Column(
            "content_type",
            postgresql.ENUM(name="content_type", create_type=False),
            server_default="text",
            nullable=False,
        ),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("transcript", sa.Text(), nullable=True),
        sa.Column(
            "transcription_status",
            postgresql.ENUM(name="transcription_status", create_type=False),
            server_default="none",
            nullable=False,
        ),
        sa.Column("media", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM(name="message_status", create_type=False),
            nullable=False,
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("reply_to_external_id", sa.Text(), nullable=True),
        sa.Column("llm_run_id", sa.Uuid(), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("external_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["channel_instance_id"],
            ["channel_instances.id"],
            name=op.f("fk_messages_channel_instance_id_channel_instances"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_messages_conversation_id_conversations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["llm_run_id"],
            ["llm_runs.id"],
            name=op.f("fk_messages_llm_run_id_llm_runs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["sender_user_id"],
            ["users.id"],
            name=op.f("fk_messages_sender_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_messages_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_messages")),
        sa.UniqueConstraint(
            "channel_instance_id",
            "external_id",
            name=op.f("uq_messages_channel_instance_id_external_id"),
        ),
    )
    op.create_index(
        "ix_messages_conversation_id_created_at",
        "messages",
        ["conversation_id", sa.literal_column("created_at DESC")],
        unique=False,
    )
    op.create_index(
        "ix_messages_outbound_queue",
        "messages",
        ["status"],
        unique=False,
        postgresql_where=sa.text("status IN ('queued', 'sending')"),
    )
    op.create_index(
        "ix_messages_pending_inbound",
        "messages",
        ["conversation_id"],
        unique=False,
        postgresql_where=sa.text("direction = 'inbound' AND processed_at IS NULL"),
    )

    # 4. FK que cierra el ciclo conversations <-> messages; va al final porque
    #    messages se crea después de conversations.
    op.create_foreign_key(
        op.f("fk_conversations_summary_message_id_messages"),
        "conversations",
        "messages",
        ["summary_message_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # 5. Rol de la aplicación, permisos, funciones y políticas RLS.
    upgrade_rls()


def downgrade() -> None:
    downgrade_rls()
    op.drop_constraint(
        op.f("fk_conversations_summary_message_id_messages"), "conversations", type_="foreignkey"
    )

    op.drop_index(
        "ix_messages_pending_inbound",
        table_name="messages",
        postgresql_where=sa.text("direction = 'inbound' AND processed_at IS NULL"),
    )
    op.drop_index(
        "ix_messages_outbound_queue",
        table_name="messages",
        postgresql_where=sa.text("status IN ('queued', 'sending')"),
    )
    op.drop_index("ix_messages_conversation_id_created_at", table_name="messages")
    op.drop_table("messages")
    op.drop_index("ix_llm_runs_tenant_id_created_at", table_name="llm_runs")
    op.drop_index("ix_llm_runs_conversation_id", table_name="llm_runs")
    op.drop_table("llm_runs")
    op.drop_index("ix_handoff_events_conversation_id_created_at", table_name="handoff_events")
    op.drop_table("handoff_events")
    op.drop_index("ix_webhook_events_status_received_at", table_name="webhook_events")
    op.drop_index("ix_webhook_events_received_at", table_name="webhook_events")
    op.drop_table("webhook_events")
    op.drop_index("ix_knowledge_chunks_tsv", table_name="knowledge_chunks", postgresql_using="gin")
    op.drop_index("ix_knowledge_chunks_tenant_id", table_name="knowledge_chunks")
    op.drop_index(
        "ix_knowledge_chunks_embedding_hnsw",
        table_name="knowledge_chunks",
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.drop_table("knowledge_chunks")
    op.drop_index(
        "uq_conversations_open_per_contact",
        table_name="conversations",
        postgresql_where=sa.text("status = 'open'"),
    )
    op.drop_index("ix_conversations_tenant_id_status_last_message_at", table_name="conversations")
    op.drop_index(
        "ix_conversations_followup",
        table_name="conversations",
        postgresql_where=sa.text("needs_followup"),
    )
    op.drop_index("ix_conversations_contact_id", table_name="conversations")
    op.drop_table("conversations")
    op.drop_table("agent_versions")
    op.drop_table("usage_daily")
    op.drop_index("ix_tenant_memberships_user_id", table_name="tenant_memberships")
    op.drop_table("tenant_memberships")
    op.drop_index("ix_refresh_tokens_user_id_expires_at", table_name="refresh_tokens")
    op.drop_table("refresh_tokens")
    op.drop_index("ix_knowledge_sources_tenant_id_status", table_name="knowledge_sources")
    op.drop_table("knowledge_sources")
    op.drop_table("external_data_sources")
    op.drop_index("ix_contacts_tenant_id_phone_e164", table_name="contacts")
    op.drop_index(
        "ix_contacts_display_name_trgm",
        table_name="contacts",
        postgresql_using="gin",
        postgresql_ops={"display_name": "gin_trgm_ops"},
    )
    op.drop_table("contacts")
    op.drop_index("ix_channel_instances_tenant_id_status", table_name="channel_instances")
    op.drop_table("channel_instances")
    op.drop_index("ix_catalog_items_tenant_id_is_active", table_name="catalog_items")
    op.drop_index(
        "ix_catalog_items_name_trgm",
        table_name="catalog_items",
        postgresql_using="gin",
        postgresql_ops={"name": "gin_trgm_ops"},
    )
    op.drop_index(
        "ix_catalog_items_embedding_hnsw",
        table_name="catalog_items",
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.drop_table("catalog_items")
    op.drop_index("ix_audit_logs_tenant_id_created_at", table_name="audit_logs")
    op.drop_index("ix_audit_logs_entity_type_entity_id", table_name="audit_logs")
    op.drop_table("audit_logs")
    op.drop_table("agents")
    op.drop_table("users")
    op.drop_table("tenants")

    bind = op.get_bind()
    for name in reversed(ENUMS):
        postgresql.ENUM(name=name).drop(bind, checkfirst=True)
    for ext in reversed(EXTENSIONS):
        op.execute(f"DROP EXTENSION IF EXISTS {ext}")
