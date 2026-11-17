// Invitaciones del lado de quien las recibe (todavía sin sesión): /auth/invitations.
import { api } from "./client";
import type { AcceptInvitationIn, InvitationPreview, Session } from "./types";

export const invitationApi = {
  preview: (token: string) =>
    api<InvitationPreview>("/auth/invitations/preview", { method: "POST", body: { token }, auth: false }),
  // Deja la sesión abierta con ese negocio elegido (usar desde useAuth().acceptInvitation).
  accept: (body: AcceptInvitationIn) =>
    api<Session>("/auth/invitations/accept", { method: "POST", body, auth: false }),
};
