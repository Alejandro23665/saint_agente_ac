// Lo que devuelve la API (ver backend/app/api/*.py). Los nombres siguen a los del backend.

export type Role = "owner" | "admin" | "agent";

// --- Sesión (/auth) ---

export interface Tenant {
  id: string;
  name: string;
  slug: string;
  role: Role;
}

export interface User {
  id: string;
  email: string;
  full_name: string;
}

export interface Session {
  access_token: string;
  expires_at: string;
  user: User;
  tenants: Tenant[];
  active_tenant: Tenant | null;
}

export interface InvitationPreview {
  tenant_name: string;
  email: string;
  role: Role;
  expires_at: string;
  has_account: boolean; // true: entra con su contraseña de siempre
}

export interface AcceptInvitationIn {
  token: string;
  password: string;
  full_name?: string | null;
  remember?: boolean;
}

// --- Negocio, miembros e invitaciones (/tenant) ---

export interface TenantDetail {
  id: string;
  name: string;
  slug: string;
  country_code: string;
  timezone: string;
  my_role: Role;
  can_edit: boolean;
  can_manage_members: boolean;
}

export interface TenantUpdateIn {
  name?: string;
  country_code?: string;
  timezone?: string;
}

export interface Member {
  user_id: string;
  email: string;
  full_name: string;
  role: Role;
  joined_at: string;
  last_login_at: string | null;
  is_me: boolean;
}

export interface Invitation {
  id: string;
  email: string;
  role: Role;
  expires_at: string;
  created_at: string;
  expired: boolean;
}

export interface NewInvitation extends Invitation {
  link: string; // se muestra una sola vez
}

// --- Números de WhatsApp (/numbers) ---

export type NumberStatus = "created" | "qr_pending" | "open" | "connecting" | "close" | "banned";

export interface WhatsappNumber {
  id: string;
  phone: string | null;
  status: NumberStatus;
  groups_enabled: boolean;
  last_connected_at: string | null;
  created_at: string;
}

export interface Qr {
  image: string | null; // data:image/png;base64,...
  pairing_code: string | null; // 8 letras para vincular sin cámara
}

export interface NumberWithQr {
  number: WhatsappNumber;
  qr: Qr | null; // null: ya está conectado
}

// --- Bandeja (/conversations) ---

export type ConversationMode = "bot" | "human" | "paused";
export type ConversationStatus = "open" | "closed";

export interface Conversation {
  id: string;
  status: ConversationStatus;
  mode: ConversationMode;
  contact: { id: string; name: string | null; phone: string | null };
  number_id: string;
  number_phone: string | null;
  assigned: { user_id: string; name: string | null } | null;
  last_message: { text: string | null; direction: MessageDirection } | null;
  last_message_at: string | null;
  waiting_reply: boolean;
  created_at: string;
}

export interface ConversationFilters {
  status?: ConversationStatus;
  mode?: ConversationMode;
  mine?: boolean;
  number_id?: string;
  q?: string;
  before?: string;
  limit?: number;
}

export type MessageDirection = "inbound" | "outbound";
export type SenderType = "contact" | "bot" | "human_panel" | "human_phone" | "system";
export type MessageStatus = "received" | "queued" | "sending" | "sent" | "delivered" | "read" | "failed";

export interface Message {
  id: string;
  direction: MessageDirection;
  sender_type: SenderType;
  sender_name: string | null;
  content_type: string; // text | audio | image | video | document | sticker | location...
  text: string | null;
  transcript: string | null;
  status: MessageStatus;
  error: string | null;
  created_at: string;
}

// --- Agente y horario (/agent, /hours) ---

export interface Agent {
  name: string;
  instructions: string;
  model: string;
  version: number;
  updated_at: string;
  can_edit: boolean;
}

export interface AgentUpdateIn {
  name?: string;
  instructions?: string;
}

export interface AgentVersion {
  version: number;
  instructions: string;
  created_at: string;
  created_by: string | null; // null: la versión de fábrica
  is_current: boolean;
}

export type DayKey = "mon" | "tue" | "wed" | "thu" | "fri" | "sat" | "sun";
// [["08:00", "12:00"], ["14:00", "18:00"]]
export type DayRanges = string[][];

export interface HoursIn {
  days: Partial<Record<DayKey, DayRanges>>;
  after_hours_message: string | null;
}

export interface Hours extends HoursIn {
  timezone: string;
  open_now: boolean;
  can_edit: boolean;
}

// --- Conocimiento (/knowledge) ---

export type KnowledgeStatus = "pending" | "processing" | "ready" | "failed";

export interface KnowledgeSource {
  id: string;
  title: string;
  source_type: string;
  status: KnowledgeStatus;
  error: string | null;
  chunk_count: number;
  characters: number;
  created_at: string;
  updated_at: string;
  created_by: string | null;
  can_edit: boolean;
}

export interface KnowledgeChunk {
  index: number;
  content: string;
  token_count: number | null;
  has_embedding: boolean;
}

export interface KnowledgeSourceDetail extends KnowledgeSource {
  chunks: KnowledgeChunk[];
}

export interface KnowledgeUpload extends KnowledgeSource {
  already_loaded: boolean; // el mismo texto ya estaba listo
}

// --- Consumo (/usage) ---

export type UsagePeriod = "this_month" | "last_month" | "last_7_days" | "last_30_days";

export interface UsageDay {
  day: string; // 2026-11-16, en la zona horaria del negocio
  conversations: number;
  inbound_messages: number;
  outbound_messages: number;
  ai_replies: number;
  input_tokens: number;
  output_tokens: number;
}

export type UsageTotals = Omit<UsageDay, "day">;

export interface UsageReport {
  period: UsagePeriod;
  start: string;
  end: string;
  timezone: string;
  // Primer día con datos: antes de esa fecha el consumo no se registraba.
  tracking_since: string | null;
  totals: UsageTotals;
  pricing: { configured: boolean; input_usd_per_million: number | null; output_usd_per_million: number | null };
  estimated_cost_usd: number | null;
  days: UsageDay[];
}
// Límites que también comprueba la API.
export const LIMITS = {
  password: 10,
  personName: 120,
  tenantName: 120,
  agentName: 80,
  instructions: 8000,
  instructionsMin: 20,
  reply: 4096,
  afterHoursMessage: 500,
  knowledgeTitle: 200,
  knowledgeChars: 200_000,
  numbers: 5,
} as const;
