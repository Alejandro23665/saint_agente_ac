/**
 * La sesión del panel: quién entró, sus negocios y cuál está elegido.
 *
 * Al abrir el panel se intenta renovar el pase con la cookie (así no hay que entrar de
 * nuevo cada vez). El pase dura 15 minutos y se renueva solo un minuto antes de vencer.
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";

import { invitationApi } from "../api/auth";
import { api, ApiError, configureSession, refreshSession, storeSession } from "../api/client";
import type { AcceptInvitationIn, Role, Session } from "../api/types";

type Status = "loading" | "anonymous" | "ready";
// Por qué se volvió al inicio de sesión (para explicarlo allí).
export type Notice = "expired" | "logged-out" | null;

interface SessionContextValue {
  status: Status;
  session: Session | null;
  notice: Notice;
  login: (email: string, password: string, remember: boolean) => Promise<void>;
  logout: () => Promise<void>;
  selectTenant: (tenantId: string) => Promise<void>;
  // Acepta una invitación y entra con ese negocio elegido.
  acceptInvitation: (body: AcceptInvitationIn) => Promise<void>;
}

const SessionContext = createContext<SessionContextValue | null>(null);

const RENEW_BEFORE_MS = 60_000;

export function SessionProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<Status>("loading");
  const [session, setSession] = useState<Session | null>(null);
  const [notice, setNotice] = useState<Notice>(null);
  const timer = useRef<number | undefined>(undefined);

  const enter = useCallback((next: Session) => {
    storeSession(next);
    setSession(next);
    setStatus("ready");
    setNotice(null);
  }, []);

  const leave = useCallback((why: Notice) => {
    storeSession(null);
    setSession(null);
    setStatus("anonymous");
    setNotice(why);
  }, []);

  useEffect(() => {
    configureSession({ lost: () => leave("expired"), renewed: enter });
  }, [enter, leave]);

  // Al abrir: ¿sigue viva la sesión de este navegador?
  useEffect(() => {
    refreshSession()
      .then((s) => (s ? enter(s) : leave(null)))
      .catch(() => leave(null));
  }, [enter, leave]);

  // Renovar el pase antes de que venza.
  useEffect(() => {
    window.clearTimeout(timer.current);
    if (!session) return;
    const wait = new Date(session.expires_at).getTime() - Date.now() - RENEW_BEFORE_MS;
    timer.current = window.setTimeout(
      () => {
        refreshSession(session.active_tenant?.id ?? null)
          .then((s) => (s ? enter(s) : leave("expired")))
          .catch(() => {
            // Sin internet un momento: se reintenta en 30 s.
            timer.current = window.setTimeout(() => setSession((s) => (s ? { ...s } : s)), 30_000);
          });
      },
      Math.max(wait, 5_000),
    );
    return () => window.clearTimeout(timer.current);
  }, [session, enter, leave]);

  const login = useCallback(
    async (email: string, password: string, remember: boolean) => {
      const next = await api<Session>("/auth/login", {
        method: "POST",
        body: { email, password, remember },
        auth: false,
      });
      enter(next);
    },
    [enter],
  );

  const logout = useCallback(async () => {
    try {
      await api<void>("/auth/logout", { method: "POST", auth: false });
    } catch (error) {
      if (!(error instanceof ApiError)) throw error;
      // Aunque falle la red, en este navegador la sesión se cierra igual.
    }
    leave("logged-out");
  }, [leave]);

  const selectTenant = useCallback(
    async (tenantId: string) => {
      const next = await api<Session>("/auth/select-tenant", {
        method: "POST",
        body: { tenant_id: tenantId },
      });
      enter(next);
    },
    [enter],
  );

  const acceptInvitation = useCallback(
    async (body: AcceptInvitationIn) => {
      enter(await invitationApi.accept(body));
    },
    [enter],
  );

  const value = useMemo(
    () => ({ status, session, notice, login, logout, selectTenant, acceptInvitation }),
    [status, session, notice, login, logout, selectTenant, acceptInvitation],
  );
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useAuth(): SessionContextValue {
  const value = useContext(SessionContext);
  if (!value) throw new Error("useAuth fuera de SessionProvider");
  return value;
}

export const ROLE_NAMES: Record<string, string> = {
  owner: "Dueño",
  admin: "Administrador",
  agent: "Agente",
};

export function initials(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean);
  const letters = words.length > 1 ? words[0][0] + words[1][0] : (words[0] ?? "?").slice(0, 2);
  return letters.toUpperCase();
}

export interface RoleInfo {
  role: Role | null;
  isOwner: boolean;
  // Dueño y administrador: cambiar el negocio, el agente, los números y el conocimiento.
  canEdit: boolean;
}

/**
 * El rol en el negocio activo, para mostrar u ocultar botones. Es solo para la vista:
 * la API vuelve a comprobarlo en cada petición (y responde can_edit donde aplica).
 */
export function useRole(): RoleInfo {
  const { session } = useAuth();
  const role = session?.active_tenant?.role ?? null;
  return { role, isOwner: role === "owner", canEdit: role === "owner" || role === "admin" };
}
