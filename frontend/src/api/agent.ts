// Agente y horario: /agent y /hours (ver backend/app/api/agent.py).
import { api } from "./client";
import type { Agent, AgentUpdateIn, AgentVersion, Hours, HoursIn } from "./types";

export const agentApi = {
  get: () => api<Agent>("/agent"),
  // Cada cambio de instrucciones crea una versión nueva.
  update: (changes: AgentUpdateIn) => api<Agent>("/agent", { method: "PATCH", body: changes }),
  defaultInstructions: () => api<{ instructions: string }>("/agent/default-instructions"),
  versions: () => api<AgentVersion[]>("/agent/versions"),
  restore: (version: number) => api<Agent>(`/agent/versions/${version}/restore`, { method: "POST" }),
  hours: () => api<Hours>("/hours"),
  setHours: (hours: HoursIn) => api<Hours>("/hours", { method: "PUT", body: hours }),
};
