import { api, configureSession, storeSession } from "../api/client";
import type { Session } from "../api/types";

const SESSION: Session = {
  access_token: "pase-nuevo",
  expires_at: new Date(Date.now() + 900_000).toISOString(),
  user: { id: "u1", email: "andre@saint.ai", full_name: "Andre" },
  tenants: [{ id: "t1", name: "Panadería", slug: "panaderia", role: "owner" }],
  active_tenant: { id: "t1", name: "Panadería", slug: "panaderia", role: "owner" },
};

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

afterEach(() => {
  vi.restoreAllMocks();
  storeSession(null);
});

test("con 401 renueva el pase una vez y repite la petición", async () => {
  storeSession({ ...SESSION, access_token: "pase-viejo" });
  const renewed = vi.fn();
  configureSession({ lost: vi.fn(), renewed });
  const calls: { url: string; auth: string | null; body: unknown }[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    const headers = new Headers(init?.headers);
    calls.push({ url, auth: headers.get("Authorization"), body: init?.body });
    if (url === "/auth/refresh") return json(200, SESSION);
    return headers.get("Authorization") === "Bearer pase-nuevo" ? json(200, [{ id: "n1" }]) : json(401, { detail: "Tu sesión terminó." });
  });

  expect(await api("/numbers")).toEqual([{ id: "n1" }]);
  expect(calls.map((c) => [c.url, c.auth])).toEqual([
    ["/numbers", "Bearer pase-viejo"],
    ["/auth/refresh", null],
    ["/numbers", "Bearer pase-nuevo"],
  ]);
  // Pide seguir en el mismo negocio.
  expect(JSON.parse(String(calls[1].body))).toEqual({ tenant_id: "t1" });
  expect(renewed).toHaveBeenCalledWith(SESSION);
});

test("si no se puede renovar, avisa que la sesión se perdió", async () => {
  storeSession(SESSION);
  const lost = vi.fn();
  configureSession({ lost, renewed: vi.fn() });
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => json(401, { detail: "Tu sesión terminó." }));
  await expect(api("/numbers")).rejects.toMatchObject({ status: 401, message: "Tu sesión terminó." });
  expect(lost).toHaveBeenCalledOnce();
});

test("los errores de la API llegan con su mensaje", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => json(403, { detail: "Tu rol no te permite" }));
  await expect(api("/knowledge", { method: "POST", body: {} })).rejects.toMatchObject({ status: 403, message: "Tu rol no te permite" });
});

test("sin red, un mensaje claro", async () => {
  vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("Failed to fetch"));
  await expect(api("/numbers")).rejects.toMatchObject({ status: 0 });
});
