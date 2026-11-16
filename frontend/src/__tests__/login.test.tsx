import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

import { App } from "../App";
import { storeSession } from "../api/client";
import { SessionProvider } from "../auth/session";

const USER = { id: "u1", email: "andre@saint.ai", full_name: "Andre Pérez" };
const PANADERIA = { id: "t1", name: "Panadería Prueba", slug: "panaderia", role: "owner" };
const TALLER = { id: "t2", name: "Taller Rápido", slug: "taller", role: "agent" };

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function session(tenants: unknown[], active: unknown) {
  return {
    access_token: "pase",
    expires_at: new Date(Date.now() + 900_000).toISOString(),
    user: USER,
    tenants,
    active_tenant: active,
  };
}

// La API simulada: sin sesión guardada; el resto responde vacío.
function fakeApi(login: (body: Record<string, unknown>) => Response) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    if (url === "/auth/refresh") return json(401, { detail: "Tu sesión terminó." });
    if (url === "/auth/login") return login(JSON.parse(String(init?.body)));
    if (url === "/auth/select-tenant") return json(200, session([PANADERIA, TALLER], TALLER));
    if (url === "/events") return new Response(null, { status: 503 });
    if (url === "/hours") return json(200, { days: {}, open_now: true, timezone: "America/Caracas" });
    if (url === "/agent") return json(200, { version: 1 });
    if (url.startsWith("/conversations"))
      return json(200, [{ id: "c1", status: "open", mode: "bot", waiting_reply: true, assigned: null, last_message_at: null }]);
    if (url === "/numbers") return json(200, [{ id: "n1", phone: "+58 412 0000000", status: "open" }]);
    return json(200, []);
  });
}

function renderApp() {
  return render(
    <MemoryRouter initialEntries={["/"]}>
      <SessionProvider>
        <App />
      </SessionProvider>
    </MemoryRouter>,
  );
}

afterEach(() => {
  vi.restoreAllMocks();
  storeSession(null);
  localStorage.clear();
});

test("correo o contraseña incorrectos", async () => {
  fakeApi(() => json(401, { detail: "Correo o contraseña incorrectos" }));
  renderApp();
  const user = userEvent.setup();
  await user.type(await screen.findByLabelText("Correo"), "andre@saint.ai");
  await user.type(screen.getByLabelText("Contraseña"), "mala");
  await user.click(screen.getByRole("button", { name: "Iniciar sesión" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("No pudimos iniciar sesión");
});

test("un correo incompleto se avisa sin llamar a la API", async () => {
  const fetch = fakeApi(() => json(500, {}));
  renderApp();
  const user = userEvent.setup();
  await user.type(await screen.findByLabelText("Correo"), "andre@saint");
  await user.click(screen.getByRole("button", { name: "Iniciar sesión" }));
  expect(screen.getByText(/Escribe un correo completo/)).toBeInTheDocument();
  expect(fetch.mock.calls.some(([url]) => url === "/auth/login")).toBe(false);
});

test("con un solo negocio entra directo al inicio con sus datos", async () => {
  fakeApi((body) => {
    expect(body).toEqual({ email: "andre@saint.ai", password: "una-clave-larga", remember: true });
    return json(200, session([PANADERIA], PANADERIA));
  });
  renderApp();
  const user = userEvent.setup();
  await user.type(await screen.findByLabelText("Correo"), "andre@saint.ai");
  await user.type(screen.getByLabelText("Contraseña"), "una-clave-larga");
  await user.click(screen.getByRole("button", { name: "Iniciar sesión" }));
  expect(await screen.findByRole("heading", { name: "Hola, Andre" })).toBeInTheDocument();
  await waitFor(() => expect(screen.getAllByText("Conectado").length).toBeGreaterThan(0));
  expect(screen.getAllByText("Agente IA activo").length).toBeGreaterThan(0);
  expect(screen.getByLabelText("1 esperan respuesta")).toBeInTheDocument();
});

test("con varios negocios pide elegir uno", async () => {
  fakeApi(() => json(200, session([PANADERIA, TALLER], null)));
  renderApp();
  const user = userEvent.setup();
  await user.type(await screen.findByLabelText("Correo"), "andre@saint.ai");
  await user.type(screen.getByLabelText("Contraseña"), "una-clave-larga");
  await user.click(screen.getByRole("button", { name: "Iniciar sesión" }));
  expect(await screen.findByRole("heading", { name: "¿Qué negocio quieres atender?" })).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: /Taller Rápido/ }));
  expect(await screen.findByText(/Así va Taller Rápido hoy/)).toBeInTheDocument();
});
