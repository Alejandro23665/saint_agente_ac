"""Listas cerradas de valores. Cada una se crea en PostgreSQL como un tipo ENUM."""

from enum import StrEnum

from sqlalchemy import Enum as SAEnum


def pg_enum(enum_cls: type[StrEnum], name: str) -> SAEnum:
    """Tipo ENUM de PostgreSQL que guarda el valor ('open'), no el nombre ('OPEN')."""
    return SAEnum(enum_cls, name=name, values_callable=lambda e: [m.value for m in e])


class BillingMode(StrEnum):
    FIXED = "fixed"
    USAGE = "usage"


class MembershipRole(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    AGENT = "agent"


class Channel(StrEnum):
    WHATSAPP = "whatsapp"
    INSTAGRAM = "instagram"


class ChannelProvider(StrEnum):
    EVOLUTION = "evolution"  # conexión por QR a través de Evolution API
    META_CLOUD = "meta_cloud"  # API oficial de Meta


class InstanceStatus(StrEnum):
    CREATED = "created"
    QR_PENDING = "qr_pending"
    OPEN = "open"
    CONNECTING = "connecting"
    CLOSE = "close"
    BANNED = "banned"


class ConversationStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"


class ConversationMode(StrEnum):
    BOT = "bot"
    HUMAN = "human"
    PAUSED = "paused"


class MessageDirection(StrEnum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class SenderType(StrEnum):
    CONTACT = "contact"
    BOT = "bot"
    HUMAN_PANEL = "human_panel"
    HUMAN_PHONE = "human_phone"
    SYSTEM = "system"


class ContentType(StrEnum):
    TEXT = "text"
    AUDIO = "audio"
    IMAGE = "image"
    VIDEO = "video"
    DOCUMENT = "document"
    STICKER = "sticker"
    LOCATION = "location"


class MessageStatus(StrEnum):
    RECEIVED = "received"
    QUEUED = "queued"
    SENDING = "sending"
    SENT = "sent"
    DELIVERED = "delivered"
    READ = "read"
    FAILED = "failed"


class TranscriptionStatus(StrEnum):
    NONE = "none"  # el mensaje no es audio
    PENDING = "pending"
    DONE = "done"
    FAILED = "failed"


class WebhookEventStatus(StrEnum):
    PENDING = "pending"
    PROCESSED = "processed"
    FAILED = "failed"
    IGNORED = "ignored"


class HandoffReason(StrEnum):
    PANEL = "panel"  # un humano escribió desde el panel
    PHONE = "phone"  # un humano respondió desde el teléfono
    AGENT_TOOL = "agent_tool"  # el agente de IA pidió pasar a humano
    CUSTOMER_REQUEST = "customer_request"
    LOW_CONFIDENCE = "low_confidence"
    ERRORS = "errors"
    RETURNED = "returned"  # el humano devolvió la conversación al bot


class AgentKind(StrEnum):
    ROUTER = "router"
    SPECIALIST = "specialist"


class LLMRunStatus(StrEnum):
    OK = "ok"
    ERROR = "error"
    DISCARDED = "discarded"  # se descartó porque llegaron mensajes nuevos


class KnowledgeSourceType(StrEnum):
    FILE = "file"
    URL = "url"
    TEXT = "text"


class ProcessingStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


class CatalogItemType(StrEnum):
    PRODUCT = "product"
    SERVICE = "service"


class DataSourceEngine(StrEnum):
    POSTGRES = "postgres"
    MYSQL = "mysql"
    SQLSERVER = "sqlserver"
    REST_API = "rest_api"
    GOOGLE_SHEETS = "google_sheets"


class ConnectionStatus(StrEnum):
    PENDING = "pending"
    OK = "ok"
    ERROR = "error"
