// Conocimiento: /knowledge (ver backend/app/api/knowledge.py).
import { api } from "./client";
import type { KnowledgeSource, KnowledgeSourceDetail, KnowledgeUpload } from "./types";

export const knowledgeApi = {
  list: () => api<KnowledgeSource[]>("/knowledge"),
  get: (id: string) => api<KnowledgeSourceDetail>(`/knowledge/${id}`),
  // 202: queda "processing"; llega knowledge.changed por /events al terminar.
  upload: (title: string, text: string) =>
    api<KnowledgeUpload>("/knowledge", { method: "POST", body: { title, text } }),
  retry: (id: string) => api<KnowledgeSource>(`/knowledge/${id}/retry`, { method: "POST" }),
  remove: (id: string) => api<void>(`/knowledge/${id}`, { method: "DELETE" }),
};
