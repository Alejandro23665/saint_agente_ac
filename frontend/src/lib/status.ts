/** Cómo se llama y de qué color es cada estado que devuelve la API. */
import type { Tone } from "../components/Feedback";
import type { ConversationMode, KnowledgeStatus, MessageStatus, NumberStatus } from "../api/types";

interface Label {
  label: string;
  tone: Tone;
}

export const NUMBER_STATUS: Record<NumberStatus, Label> = {
  open: { label: "Conectado", tone: "success" },
  connecting: { label: "Conectando", tone: "warn" },
  qr_pending: { label: "Falta escanear el QR", tone: "warn" },
  created: { label: "Falta escanear el QR", tone: "warn" },
  close: { label: "Desconectado", tone: "danger" },
  banned: { label: "Bloqueado por WhatsApp", tone: "danger" },
};

export const KNOWLEDGE_STATUS: Record<KnowledgeStatus, Label> = {
  pending: { label: "En espera", tone: "muted" },
  processing: { label: "Procesando", tone: "warn" },
  ready: { label: "Listo", tone: "success" },
  failed: { label: "Falló", tone: "danger" },
};

export const MESSAGE_STATUS: Record<MessageStatus, Label> = {
  received: { label: "Recibido", tone: "muted" },
  queued: { label: "En cola", tone: "muted" },
  sending: { label: "Enviando", tone: "muted" },
  sent: { label: "Enviado", tone: "ice" },
  delivered: { label: "Entregado", tone: "ice" },
  read: { label: "Leído", tone: "success" },
  failed: { label: "No se envió", tone: "danger" },
};

export const MODE: Record<ConversationMode, Label> = {
  bot: { label: "Agente IA", tone: "ice" },
  human: { label: "Persona", tone: "warn" },
  paused: { label: "En pausa", tone: "muted" },
};

const FALLBACK: Label = { label: "Desconocido", tone: "muted" };

export function statusOf<K extends string>(table: Record<K, Label>, key: string): Label {
  return (table as Record<string, Label>)[key] ?? { ...FALLBACK, label: key || FALLBACK.label };
}
