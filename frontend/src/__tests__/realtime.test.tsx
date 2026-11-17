import { render, screen, waitFor } from "@testing-library/react";

import { storeSession } from "../api/client";
import { DashboardProvider, useDashboard, useEvents } from "../layout/dashboard";

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
}

afterEach(() => {
  vi.restoreAllMocks();
  storeSession(null);
});

test("las pantallas reciben los avisos de /events sin abrir otra conexión", async () => {
  storeSession({
    access_token: "pase",
    expires_at: new Date(Date.now() + 900_000).toISOString(),
    user: { id: "u1", email: "a@b.co", full_name: "Andre" },
    tenants: [],
    active_tenant: null,
  });
  let numbersCalls = 0;
  let eventsCalls = 0;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = String(input);
    if (url === "/events") {
      eventsCalls += 1;
      if (eventsCalls > 1) return new Promise<Response>(() => {}); // se queda abierta
      return new Response(
        'event: ready\ndata: {"type": "ready"}\n\nevent: numbers.changed\ndata: {"type": "numbers.changed"}\n\n',
        { status: 200 },
      );
    }
    if (url === "/numbers") {
      numbersCalls += 1;
      return json([{ id: "n1", phone: null, status: numbersCalls > 1 ? "open" : "qr_pending" }]);
    }
    return json([]);
  });

  const seen: string[] = [];
  function Screen() {
    useEvents(["numbers.changed"], (event) => seen.push(event.type));
    const { numbers } = useDashboard();
    return <span>{numbers?.[0]?.status ?? "cargando"}</span>;
  }

  render(
    <DashboardProvider tenantId="t1">
      <Screen />
    </DashboardProvider>,
  );

  // Carga inicial y, con el aviso numbers.changed, recarga solo los números.
  expect(await screen.findByText("open", {}, { timeout: 2000 })).toBeInTheDocument();
  await waitFor(() => expect(seen).toEqual(["numbers.changed"]));
  expect(numbersCalls).toBe(2);
});
