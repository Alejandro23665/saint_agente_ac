import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { App } from "../App";
import { storeSession } from "../api/client";
import { SessionProvider } from "../auth/session";
import { ToastProvider } from "../components/Toast";

const TENANT = { id: "t1", name: "Panadería Prueba", slug: "panaderia", role: "owner" };
const SESSION = {
  access_token: "pase",
  expires_at: new Date(Date.now() + 900_000).toISOString(),
  user: { id: "u1", email: "andre@saint.ai", full_name: "Andre Pérez" },
  tenants: [TENANT],
  active_tenant: TENANT,
};

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function fakeApi(counts: unknown) {
  const urls: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = String(input);
    urls.push(url);
    const path = new URL(url, "http://x").pathname;
    if (path === "/auth/refresh") return json(200, SESSION);
    if (path === "/events") return new Response(null, { status: 503 });
    if (path === "/conversations/counts") return json(200, counts);
    if (path === "/hours") return json(200, { days: {}, after_hours_message: null, open_now: true, timezone: "America/Caracas", can_edit: true });
    if (path === "/agent") return json(200, { version: 1 });
    return json(200, []);
  });
  return urls;
}

afterEach(() => {
  vi.restoreAllMocks();
  storeSession(null);
  localStorage.clear();
});

test("Inicio muestra los contadores de todas las conversaciones abiertas, sin tope de 100", async () => {
  const urls = fakeApi({ open: 1250, waiting: 37, human: 4, mine: 2 });
  render(
    <MemoryRouter initialEntries={["/"]}>
      <SessionProvider>
        <ToastProvider>
          <App />
        </ToastProvider>
      </SessionProvider>
    </MemoryRouter>,
  );
  const stats = await screen.findByRole("region", { name: "Resumen de la bandeja" });
  const open = await within(stats).findByRole("link", { name: /Conversaciones abiertas/ });
  expect(open).toHaveTextContent("1250");
  expect(within(stats).getByRole("link", { name: /Esperan respuesta/ })).toHaveTextContent("37");
  expect(within(stats).getByRole("link", { name: /Las atiende una persona/ })).toHaveTextContent("4");
  expect(within(stats).getByRole("link", { name: /Asignadas a mí/ })).toHaveTextContent("2");
  expect(screen.queryByText("100+")).not.toBeInTheDocument();
  // El número del menú (esperan respuesta) sale del mismo contador.
  expect(screen.getAllByLabelText("37 esperan respuesta").length).toBeGreaterThan(0);
  // Ya no se piden 100 conversaciones solo para contar.
  expect(urls.some((u) => u.includes("limit=100"))).toBe(false);
});
