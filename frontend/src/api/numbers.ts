// Números de WhatsApp: /numbers (ver backend/app/api/numbers.py).
import { api } from "./client";
import type { NumberWithQr, WhatsappNumber } from "./types";

export const numbersApi = {
  list: () => api<WhatsappNumber[]>("/numbers"),
  connect: (groupsEnabled = false) =>
    api<NumberWithQr>("/numbers", { method: "POST", body: { groups_enabled: groupsEnabled } }),
  // Pregunta a WhatsApp el estado real y lo guarda.
  refresh: (id: string) => api<WhatsappNumber>(`/numbers/${id}`),
  // Con el teléfono, además del QR llega un código de 8 letras.
  newQr: (id: string, phone?: string) =>
    api<NumberWithQr>(`/numbers/${id}/qr`, { method: "POST", body: { phone: phone || null } }),
  setGroups: (id: string, enabled: boolean) =>
    api<WhatsappNumber>(`/numbers/${id}`, { method: "PATCH", body: { groups_enabled: enabled } }),
  disconnect: (id: string) => api<WhatsappNumber>(`/numbers/${id}/disconnect`, { method: "POST" }),
  remove: (id: string) => api<void>(`/numbers/${id}`, { method: "DELETE" }),
};
