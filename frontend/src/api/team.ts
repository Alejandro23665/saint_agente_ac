// Negocio, miembros e invitaciones: /tenant (ver backend/app/api/tenant.py).
import { api } from "./client";
import type { Invitation, Member, NewInvitation, Role, TenantDetail, TenantUpdateIn } from "./types";

export const teamApi = {
  tenant: () => api<TenantDetail>("/tenant"),
  updateTenant: (changes: TenantUpdateIn) => api<TenantDetail>("/tenant", { method: "PATCH", body: changes }),
  members: () => api<Member[]>("/tenant/members"),
  changeRole: (userId: string, role: Role) =>
    api<void>(`/tenant/members/${userId}`, { method: "PATCH", body: { role } }),
  // Con el propio id: salir del negocio.
  removeMember: (userId: string) => api<void>(`/tenant/members/${userId}`, { method: "DELETE" }),
  invitations: () => api<Invitation[]>("/tenant/invitations"),
  invite: (email: string, role: Role) =>
    api<NewInvitation>("/tenant/invitations", { method: "POST", body: { email, role } }),
  revokeInvitation: (id: string) => api<void>(`/tenant/invitations/${id}`, { method: "DELETE" }),
};
