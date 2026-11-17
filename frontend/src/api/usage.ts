// Consumo: /usage (ver backend/app/api/usage.py). Solo dueño y administrador.
import { api, query } from "./client";
import type { UsagePeriod, UsageReport } from "./types";

export const usageApi = {
  get: (period: UsagePeriod) => api<UsageReport>(`/usage${query({ period })}`),
};
