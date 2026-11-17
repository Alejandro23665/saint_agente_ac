/** Piezas puras de la bandeja: filtros en la dirección, mezcla de páginas y textos. */
import type { Conversation, ConversationFilters, ConversationMode } from "../api/types";
import { phone } from "./format";

export const INBOX_PAGE_SIZE = 30; // el mismo que usa la API por defecto

/** Los filtros tal como viajan en la dirección: ?status=closed&mode=human&mine=1&number=...&q=ana */
export function filtersFromParams(params: URLSearchParams): ConversationFilters {
  const mode = params.get("mode");
  return {
    status: params.get("status") === "closed" ? "closed" : "open",
    mode: mode === "bot" || mode === "human" ? (mode as ConversationMode) : undefined,
    mine: params.get("mine") === "1" || undefined,
    number_id: params.get("number") || undefined,
    q: params.get("q")?.trim() || undefined,
  };
}

/** ¿Hay algún filtro puesto aparte de "abiertas"? */
export function hasActiveFilters(filters: ConversationFilters): boolean {
  return Boolean(filters.status === "closed" || filters.mode || filters.mine || filters.number_id || filters.q);
}

function time(c: Conversation): number {
  return c.last_message_at ? Date.parse(c.last_message_at) : Number.NEGATIVE_INFINITY;
}

/**
 * Junta la primera página recién pedida con lo que ya estaba cargado, para que un aviso
 * en tiempo real no borre las páginas más antiguas que la persona ya abrió.
 * - Las filas de la página nueva mandan (cambió su estado, su último mensaje...).
 * - Se conservan las filas más antiguas que la última de la página nueva.
 * - Las demás ya no cumplen el filtro (se cerraron, las tomó alguien...) y se quitan.
 */
export function mergeFirstPage(previous: Conversation[], fresh: Conversation[], pageSize = INBOX_PAGE_SIZE): Conversation[] {
  if (fresh.length < pageSize) return fresh;
  const oldest = time(fresh[fresh.length - 1]);
  const ids = new Set(fresh.map((c) => c.id));
  return [...fresh, ...previous.filter((c) => !ids.has(c.id) && time(c) < oldest)];
}

export function contactName(c: Pick<Conversation, "contact">): string {
  return c.contact.name?.trim() || (c.contact.phone ? phone(c.contact.phone) : "Contacto sin nombre");
}

/** Lo que se ve debajo del nombre: el último mensaje, o por qué no hay texto. */
export function previewText(c: Pick<Conversation, "last_message">): string {
  if (!c.last_message) return "Sin mensajes todavía";
  const text = c.last_message.text?.trim();
  const body = text ? text.replace(/\s+/g, " ") : "Mensaje sin texto (audio, foto u otro archivo)";
  return c.last_message.direction === "outbound" ? `Tú: ${body}` : body;
}
