"""Importa todos los modelos para que Base.metadata conozca las 19 tablas.

Alembic lee Base.metadata; si un modelo no se importa aquí, Alembic no lo ve.
"""

from app.models.agent import Agent
from app.models.agent_version import AgentVersion
from app.models.audit_log import AuditLog
from app.models.catalog_item import CatalogItem
from app.models.channel_instance import ChannelInstance
from app.models.contact import Contact
from app.models.conversation import Conversation
from app.models.external_data_source import ExternalDataSource
from app.models.handoff_event import HandoffEvent
from app.models.knowledge_chunk import KnowledgeChunk
from app.models.knowledge_source import KnowledgeSource
from app.models.llm_run import LLMRun
from app.models.message import Message
from app.models.refresh_token import RefreshToken
from app.models.tenant import Tenant
from app.models.tenant_membership import TenantMembership
from app.models.usage_daily import UsageDaily
from app.models.user import User
from app.models.webhook_event import WebhookEvent

__all__ = [
    "Agent",
    "AgentVersion",
    "AuditLog",
    "CatalogItem",
    "ChannelInstance",
    "Contact",
    "Conversation",
    "ExternalDataSource",
    "HandoffEvent",
    "KnowledgeChunk",
    "KnowledgeSource",
    "LLMRun",
    "Message",
    "RefreshToken",
    "Tenant",
    "TenantMembership",
    "UsageDaily",
    "User",
    "WebhookEvent",
]
