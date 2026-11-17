// Bandeja: /conversations (ver backend/app/api/conversations.py).
import { api, query } from "./client";
import type { Conversation, ConversationFilters, Message } from "./types";

export const inboxApi = {
  list: (filters: ConversationFilters = {}) => api<Conversation[]>(`/conversations${query({ ...filters })}`),
  get: (id: string) => api<Conversation>(`/conversations/${id}`),
  // El más viejo primero. Para ver anteriores: before = created_at del primero.
  messages: (id: string, before?: string, limit?: number) =>
    api<Message[]>(`/conversations/${id}/messages${query({ before, limit })}`),
  // 202: queda en la cola de salida; el estado se ve después en los mensajes.
  reply: (id: string, text: string) =>
    api<{ message_id: string }>(`/conversations/${id}/messages`, { method: "POST", body: { text } }),
  take: (id: string) => api<void>(`/conversations/${id}/take`, { method: "POST" }),
  release: (id: string) => api<void>(`/conversations/${id}/release`, { method: "POST" }),
  // user_id null: quitar la asignación.
  assign: (id: string, userId: string | null) =>
    api<void>(`/conversations/${id}/assign`, { method: "POST", body: { user_id: userId } }),
  close: (id: string) => api<void>(`/conversations/${id}/close`, { method: "POST" }),
};
