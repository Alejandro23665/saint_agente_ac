/**
 * Cliente de la API.
 *
 * - El pase de acceso (15 min) vive solo en memoria: nunca en localStorage, así un
 *   script ajeno no puede llevárselo.
 * - El pase de renovación es una cookie HttpOnly que el navegador manda solo a /auth.
 * - Si una petición responde 401, se renueva el pase una vez y se repite. Si tampoco
 *   se puede renovar, se avisa (onSessionLost) y el panel vuelve al inicio de sesión.
 */
import type { Session } from "./types";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

const TENANT_KEY = "saint.negocio";
const NETWORK_ERROR = "No pudimos conectar con Saint. Revisa tu internet y vuelve a intentar.";

let accessToken: string | null = null;
let refreshing: Promise<Session | null> | null = null;
let onSessionLost: () => void = () => {};
let onSessionRenewed: (session: Session) => void = () => {};

export function configureSession(handlers: {
  lost: () => void;
  renewed: (session: Session) => void;
}): void {
  onSessionLost = handlers.lost;
  onSessionRenewed = handlers.renewed;
}

export function getAccessToken(): string | null {
  return accessToken;
}

/** Guarda el pase nuevo y recuerda (en este navegador) qué negocio estaba elegido. */
export function storeSession(session: Session | null): void {
  accessToken = session?.access_token ?? null;
  try {
    if (session?.active_tenant) localStorage.setItem(TENANT_KEY, session.active_tenant.id);
    if (session === null) localStorage.removeItem(TENANT_KEY);
  } catch {
    // Sin almacenamiento (modo privado): solo se pierde recordar el negocio.
  }
}

function rememberedTenant(): string | null {
  try {
    return localStorage.getItem(TENANT_KEY);
  } catch {
    return null;
  }
}

async function readError(response: Response): Promise<ApiError> {
  let message = "Algo salió mal. Vuelve a intentar.";
  try {
    const body = await response.json();
    if (typeof body.detail === "string") message = body.detail;
  } catch {
    // Respuesta sin JSON: se queda el mensaje general.
  }
  return new ApiError(response.status, message);
}

async function send(path: string, init: RequestInit, auth: boolean): Promise<Response> {
  const headers = new Headers(init.headers);
  if (init.body !== undefined) headers.set("Content-Type", "application/json");
  if (auth && accessToken) headers.set("Authorization", `Bearer ${accessToken}`);
  try {
    return await fetch(path, { ...init, headers, credentials: "same-origin" });
  } catch {
    throw new ApiError(0, NETWORK_ERROR);
  }
}

/**
 * Pide un pase nuevo con la cookie. Si ya hay una renovación en curso, espera esa
 * (varias peticiones con 401 a la vez no renuevan varias veces).
 */
export function refreshSession(tenantId?: string | null): Promise<Session | null> {
  if (!refreshing) {
    const wanted = tenantId === undefined ? rememberedTenant() : tenantId;
    refreshing = (async () => {
      const response = await send(
        "/auth/refresh",
        { method: "POST", body: wanted ? JSON.stringify({ tenant_id: wanted }) : undefined },
        false,
      );
      if (!response.ok) {
        if (response.status === 401) return null;
        throw await readError(response);
      }
      const session = (await response.json()) as Session;
      storeSession(session);
      onSessionRenewed(session);
      return session;
    })().finally(() => {
      refreshing = null;
    });
  }
  return refreshing;
}

/** "?a=1&b=x" con los valores que no están vacíos (o "" si no queda ninguno). */
export function query(params: Record<string, string | number | boolean | null | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "" || value === false) continue;
    search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

export async function api<T>(
  path: string,
  options: { method?: string; body?: unknown; auth?: boolean } = {},
): Promise<T> {
  const { method = "GET", body, auth = true } = options;
  const init: RequestInit = { method, body: body === undefined ? undefined : JSON.stringify(body) };
  let response = await send(path, init, auth);
  if (response.status === 401 && auth) {
    const renewed = await refreshSession().catch(() => null);
    if (!renewed) {
      storeSession(null);
      onSessionLost();
      throw await readError(response);
    }
    response = await send(path, init, auth);
  }
  if (!response.ok) throw await readError(response);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}
