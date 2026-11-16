// Lo que devuelve la API (ver backend/app/api/*.py).

export type Role = "owner" | "admin" | "agent";

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

export interface Conversation {
  id: string;
  status: "open" | "closed";
  mode: "bot" | "human" | "paused";
  waiting_reply: boolean;
  assigned: { user_id: string; name: string | null } | null;
  last_message_at: string | null;
}

export type NumberStatus = "created" | "qr_pending" | "open" | "connecting" | "close" | "banned";

export interface WhatsappNumber {
  id: string;
  phone: string | null;
  status: NumberStatus;
}

export interface Hours {
  days: Record<string, string[][]>;
  open_now: boolean;
  timezone: string;
}

export interface Agent {
  version: number;
}

export interface KnowledgeSource {
  id: string;
  status: "pending" | "processing" | "ready" | "failed";
}

export interface Member {
  user_id: string;
}
