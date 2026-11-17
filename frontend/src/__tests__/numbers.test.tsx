import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

import { App } from "../App";
import { storeSession } from "../api/client";
import { SessionProvider } from "../auth/session";
import { timing } from "../components/ConnectNumberModal";
import { ToastProvider } from "../components/Toast";

const USER = { id: "u1", email: "andre@saint.ai", full_name: "Andre Pérez" };

function json(status: number, body: unknown): Response {
  return new Response(status === 204 ? null : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function sessionAs(role: string) {
  const tenant = { id: "t1", name: "Panadería Prueba", slug: "panaderia", role };
  return {
    access_token: "pase",
    expires_at: new Date(Date.now() + 900_000).toISOString(),
    user: USER,
    tenants: [tenant],
    active_tenant: tenant,
  };
}

function number(overrides: Record<string, unknown> = {}) {
  return {
    id: "n1",
    phone: "+584121234567",
    status: "open",
    groups_enabled: false,
    last_connected_at: "2026-11-16T10:00:00Z",
    created_at: "2026-11-10T10:00:00Z",
    ...overrides,
  };
}

interface Fake {
  numbers: ReturnType<typeof number>[];
  // Lo que responde GET /numbers/{id} (el estado que "ve" WhatsApp).
  refreshStatus: string;
  calls: { method: string; url: string; body: unknown }[];
}

function fakeApi(role: string, fake: Fake) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    fake.calls.push({ method, url, body });

    if (url === "/auth/refresh") return json(200, sessionAs(role));
    if (url === "/events") return new Response(null, { status: 503 });
    if (url === "/numbers" && method === "GET") return json(200, fake.numbers);
    if (url === "/numbers" && method === "POST") {
      const created = number({ id: "n2", phone: null, status: "qr_pending", last_connected_at: null });
      fake.numbers.push(created);
      return json(201, { number: created, qr: { image: "data:image/png;base64,AAAA", pairing_code: null } });
    }
    const one = /^\/numbers\/([^/]+)(\/qr|\/disconnect)?$/.exec(url);
    if (one) {
      const found = fake.numbers.find((n) => n.id === one[1])!;
      if (one[2] === "/qr")
        return json(200, { number: found, qr: { image: "data:image/png;base64,BBBB", pairing_code: body?.phone ? "ABCD1234" : null } });
      if (one[2] === "/disconnect") {
        found.status = "close";
        return json(200, found);
      }
      if (method === "PATCH") {
        found.groups_enabled = body.groups_enabled;
        return json(200, found);
      }
      if (method === "DELETE") {
        fake.numbers = fake.numbers.filter((n) => n.id !== found.id);
        return json(204, null);
      }
      return json(200, { ...found, status: fake.refreshStatus });
    }
    if (url === "/hours") return json(200, { days: {}, after_hours_message: null, open_now: true, timezone: "America/Caracas", can_edit: true });
    if (url === "/agent") return json(200, { version: 1 });
    return json(200, []);
  });
}

function renderNumbers() {
  return render(
    <MemoryRouter initialEntries={["/numeros"]}>
      <SessionProvider>
        <ToastProvider>
          <App />
        </ToastProvider>
      </SessionProvider>
    </MemoryRouter>,
  );
}

const original = { ...timing };

afterEach(() => {
  vi.restoreAllMocks();
  storeSession(null);
  localStorage.clear();
  Object.assign(timing, original);
});

test("el dueño ve sus números y puede conectar, desconectar y borrar", async () => {
  fakeApi("owner", { numbers: [number()], refreshStatus: "open", calls: [] });
  renderNumbers();
  const card = await screen.findByRole("article", { name: "+58 412 1234567" });
  expect(within(card).getByText("Conectado")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Conectar un número" })).toBeEnabled();
  expect(within(card).getByRole("button", { name: "Desconectar" })).toBeInTheDocument();
  expect(within(card).getByRole("button", { name: "Borrar" })).toBeInTheDocument();
});

test("un agente solo lee: no ve botones para cambiar nada", async () => {
  fakeApi("agent", { numbers: [number()], refreshStatus: "open", calls: [] });
  renderNumbers();
  const card = await screen.findByRole("article", { name: "+58 412 1234567" });
  expect(screen.getByText("Solo lectura")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Conectar un número" })).not.toBeInTheDocument();
  expect(within(card).queryByRole("button", { name: "Desconectar" })).not.toBeInTheDocument();
  expect(within(card).queryByRole("button", { name: "Borrar" })).not.toBeInTheDocument();
  expect(within(card).getByRole("switch")).toBeDisabled();
  expect(within(card).getByRole("button", { name: "Revisar estado" })).toBeEnabled();
});

test("un administrador gestiona pero no puede borrar", async () => {
  fakeApi("admin", { numbers: [number()], refreshStatus: "open", calls: [] });
  renderNumbers();
  const card = await screen.findByRole("article", { name: "+58 412 1234567" });
  expect(within(card).getByRole("button", { name: "Desconectar" })).toBeInTheDocument();
  expect(within(card).queryByRole("button", { name: "Borrar" })).not.toBeInTheDocument();
});

test("sin números muestra cómo empezar", async () => {
  fakeApi("owner", { numbers: [], refreshStatus: "open", calls: [] });
  renderNumbers();
  expect(await screen.findByText("Todavía no conectas un número")).toBeInTheDocument();
});

test("conectar: muestra el QR y se cierra solo cuando el teléfono queda vinculado", async () => {
  timing.poll = 40;
  const fake: Fake = { numbers: [], refreshStatus: "qr_pending", calls: [] };
  fakeApi("owner", fake);
  renderNumbers();
  const user = userEvent.setup();

  await user.click((await screen.findAllByRole("button", { name: "Conectar un número" }))[0]);
  const dialog = await screen.findByRole("dialog", { name: "Conectar un número de WhatsApp" });
  await user.click(within(dialog).getByRole("switch"));
  await user.click(within(dialog).getByRole("button", { name: "Generar QR" }));

  expect(await within(dialog).findByAltText("Código QR para vincular WhatsApp")).toHaveAttribute("src", "data:image/png;base64,AAAA");
  expect(fake.calls.find((c) => c.method === "POST" && c.url === "/numbers")?.body).toEqual({ groups_enabled: true });

  // El teléfono escanea: la próxima consulta de estado dice "open".
  fake.refreshStatus = "open";
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  expect(screen.getAllByRole("status").some((el) => el.textContent?.includes("Número conectado"))).toBe(true);
});

test("conectar con código: pide el teléfono y muestra las 8 letras", async () => {
  timing.poll = 60_000;
  timing.renew = 60_000;
  const fake: Fake = { numbers: [number({ status: "close" })], refreshStatus: "close", calls: [] };
  fakeApi("owner", fake);
  renderNumbers();
  const user = userEvent.setup();

  const card = await screen.findByRole("article", { name: "+58 412 1234567" });
  await user.click(within(card).getByRole("button", { name: "Mostrar QR" }));
  const dialog = await screen.findByRole("dialog");
  await within(dialog).findByAltText("Código QR para vincular WhatsApp");

  await user.click(within(dialog).getByRole("button", { name: "Usar un código" }));
  await user.type(within(dialog).getByLabelText("Número del teléfono"), "+58 412 1234567");
  await user.click(within(dialog).getByRole("button", { name: "Obtener código" }));
  expect(await within(dialog).findByText("ABCD-1234")).toBeInTheDocument();
  expect(fake.calls.filter((c) => c.url === "/numbers/n1/qr").at(-1)?.body).toEqual({ phone: "+58 412 1234567" });
});

test("apagar los grupos llama a la API y avisa", async () => {
  const fake: Fake = { numbers: [number({ groups_enabled: true })], refreshStatus: "open", calls: [] };
  fakeApi("owner", fake);
  renderNumbers();
  const user = userEvent.setup();
  const card = await screen.findByRole("article", { name: "+58 412 1234567" });
  const toggle = within(card).getByRole("switch");
  expect(toggle).toBeChecked();
  await user.click(toggle);
  await waitFor(() => expect(within(card).getByRole("switch")).not.toBeChecked());
  expect(fake.calls.find((c) => c.method === "PATCH")?.body).toEqual({ groups_enabled: false });
});

test("borrar exige escribir el número y lo quita de la lista", async () => {
  const fake: Fake = { numbers: [number()], refreshStatus: "open", calls: [] };
  fakeApi("owner", fake);
  renderNumbers();
  const user = userEvent.setup();
  const card = await screen.findByRole("article", { name: "+58 412 1234567" });
  await user.click(within(card).getByRole("button", { name: "Borrar" }));

  const dialog = await screen.findByRole("dialog", { name: "Borrar el número" });
  const confirm = within(dialog).getByRole("button", { name: "Borrar número" });
  expect(confirm).toBeDisabled();
  await user.type(within(dialog).getByLabelText(/Para confirmar, escribe/), "+58 412 1234567");
  await user.click(confirm);

  await waitFor(() => expect(screen.queryByRole("article", { name: "+58 412 1234567" })).not.toBeInTheDocument());
  expect(fake.calls.some((c) => c.method === "DELETE" && c.url === "/numbers/n1")).toBe(true);
});

test("desconectar pide confirmación y cambia el estado", async () => {
  const fake: Fake = { numbers: [number()], refreshStatus: "open", calls: [] };
  fakeApi("owner", fake);
  renderNumbers();
  const user = userEvent.setup();
  const card = await screen.findByRole("article", { name: "+58 412 1234567" });
  await user.click(within(card).getByRole("button", { name: "Desconectar" }));
  const dialog = await screen.findByRole("dialog", { name: "Desconectar el número" });
  await user.click(within(dialog).getByRole("button", { name: "Desconectar" }));
  await waitFor(() => expect(within(screen.getByRole("article", { name: "+58 412 1234567" })).getByText("Desconectado")).toBeInTheDocument());
});
