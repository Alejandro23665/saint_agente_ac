/** Piezas puras de la conversación: mezclar páginas, agrupar por día y textos. */
import type { Message } from "../api/types";

export const MESSAGES_PAGE_SIZE = 50; // el mismo que usa la API por defecto

function time(m: Pick<Message, "created_at">): number {
  return Date.parse(m.created_at);
}

/**
 * Junta la página más reciente recién pedida con lo ya cargado (los mensajes van del más
 * viejo al más nuevo). Los de la página nueva mandan: ahí cambia el estado de lo enviado
 * (en cola, enviado, entregado, leído). Lo más antiguo que ya se había abierto se conserva.
 */
export function mergeLatest(previous: Message[], fresh: Message[], pageSize = MESSAGES_PAGE_SIZE): Message[] {
  if (fresh.length < pageSize) return fresh;
  const oldest = time(fresh[0]);
  const ids = new Set(fresh.map((m) => m.id));
  return [...previous.filter((m) => !ids.has(m.id) && time(m) < oldest), ...fresh];
}

/** "Hoy", "Ayer" o "lunes, 9 de noviembre". */
export function dayLabel(value: string, now: Date = new Date()): string {
  const date = new Date(value);
  const startOf = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const days = Math.round((startOf(now) - startOf(date)) / 86_400_000);
  if (days === 0) return "Hoy";
  if (days === 1) return "Ayer";
  return date.toLocaleDateString("es", {
    weekday: "long",
    day: "numeric",
    month: "long",
    year: date.getFullYear() === now.getFullYear() ? undefined : "numeric",
  });
}

export interface DayGroup {
  key: string; // 2026-11-16
  label: string;
  messages: Message[];
}

export function groupByDay(messages: Message[], now: Date = new Date()): DayGroup[] {
  const groups: DayGroup[] = [];
  for (const message of messages) {
    const d = new Date(message.created_at);
    const key = `${d.getFullYear()}-${d.getMonth() + 1}-${d.getDate()}`;
    const last = groups[groups.length - 1];
    if (last?.key === key) last.messages.push(message);
    else groups.push({ key, label: dayLabel(message.created_at, now), messages: [message] });
  }
  return groups;
}

/** Quién escribió, para la etiqueta de arriba de cada burbuja. */
export function senderLabel(m: Pick<Message, "sender_type" | "sender_name">): string | null {
  switch (m.sender_type) {
    case "bot":
      return "Agente IA";
    case "human_panel":
      return m.sender_name ? `${m.sender_name} (panel)` : "Una persona (panel)";
    case "human_phone":
      return "Desde el teléfono del negocio";
    default:
      return null;
  }
}

const MEDIA: Record<string, string> = {
  audio: "Nota de voz",
  image: "Foto",
  video: "Video",
  document: "Documento",
  sticker: "Sticker",
  location: "Ubicación",
};

export interface MessageBody {
  media: string | null; // "Foto", "Nota de voz"...
  text: string | null;
  note: string | null; // aclaración en cursiva
}

/** Lo que se dibuja dentro de la burbuja. Saint todavía no muestra los archivos: se avisa. */
export function messageBody(m: Pick<Message, "content_type" | "text" | "transcript">): MessageBody {
  const text = m.text?.trim() || null;
  if (m.content_type === "text") return { media: null, text, note: null };
  const media = MEDIA[m.content_type] ?? "Archivo";
  if (m.content_type === "audio") {
    const transcript = m.transcript?.trim();
    return transcript
      ? { media, text: transcript, note: "Transcripción automática" }
      : { media, text, note: "Todavía no se puede escuchar desde el panel" };
  }
  return { media, text, note: "El archivo se ve en el WhatsApp del negocio" };
}
