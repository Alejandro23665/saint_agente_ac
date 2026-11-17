import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

import { App } from "../App";
import { storeSession } from "../api/client";
import type { Conversation } from "../api/types";
import { SessionProvider } from "../auth/session";
import { ToastProvider } from "../components/Toast";
import { contactName, filtersFromParams, hasActiveFilters, mergeFirstPage, previewText } from "../lib/inbox";

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

function conv(i: number, overrides: Partial<Conversation> = {}): Conversation {
  return {
    id: `c${i}`,
    status: "open",
    mode: "bot",
    contact: { id: `k${i}`, name: `Cliente ${i}`, phone: `+58412000${String(i).padStart(4, "0")}` },
    number_id: "n1",
    number_phone: "+584121234567",
    assigned: null,
    last_message: { text: `Hola, soy el cliente ${i}`, direction: "inbound" },
    last_message_at: new Date(Date.UTC(2026, 10, 16, 12, 0, 0) - i * 60_000).toISOString(),
    waiting_reply: false,
    created_at: "2026-11-10T10:00:00Z",
    ...overrides,
  };
}

// API simulada: la bandeja filtra y pagina como la de verdad (status, mode, q, before, limit).
function fakeApi(rows: Conversation[]) {
  const urls: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = String(input);
    urls.push(url);
    const u = new URL(url, "http://x");
    if (u.pathname === "/auth/refresh") return json(200, SESSION);
    if (u.pathname === "/events") return new Response(null, { status: 503 });
    if (u.pathname === "/numbers") return json(200, []);
    if (u.pathname === "/conversations/counts") {
      const open = rows.filter((c) => c.status === "open");
      return json(200, {
        open: open.length,
        waiting: open.filter((c) => c.waiting_reply).length,
        human: open.filter((c) => c.mode === "human").length,
        mine: open.filter((c) => c.assigned?.user_id === SESSION.user.id).length,
      });
    }
    if (u.pathname === "/conversations") {
      const q = u.searchParams.get("q")?.toLowerCase();
      const before = u.searchParams.get("before");
      const limit = Number(u.searchParams.get("limit") ?? 30);
      const found = rows
        .filter((c) => c.status === (u.searchParams.get("status") ?? "open"))
        .filter((c) => !u.searchParams.get("mode") || c.mode === u.searchParams.get("mode"))
        .filter((c) => !q || (c.contact.name ?? "").toLowerCase().includes(q) || (c.contact.phone ?? "").includes(q))
        .filter((c) => !before || Date.parse(c.last_message_at!) < Date.parse(before))
        .slice(0, limit);
      return json(200, found);
    }
    const one = /^\/conversations\/([^/]+)$/.exec(u.pathname);
    if (one) return json(200, rows.find((c) => c.id === one[1]));
    if (u.pathname === "/hours") return json(200, { days: {}, after_hours_message: null, open_now: true, timezone: "America/Caracas", can_edit: true });
    if (u.pathname === "/agent") return json(200, { version: 1 });
    return json(200, []);
  });
  return urls;
}

function renderInbox(path = "/bandeja") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <SessionProvider>
        <ToastProvider>
          <App />
        </ToastProvider>
      </SessionProvider>
    </MemoryRouter>,
  );
}

const inboxCalls = (urls: string[]) => urls.filter((u) => u.startsWith("/conversations?") && u.includes("limit=30"));

afterEach(() => {
  vi.restoreAllMocks();
  storeSession(null);
  localStorage.clear();
});

test("la bandeja muestra quién escribió, el último mensaje y quién la atiende", async () => {
  fakeApi([
    conv(1, { waiting_reply: true, contact: { id: "k1", name: "María Pérez", phone: "+584121111111" } }),
    conv(2, { mode: "human", assigned: { user_id: "u9", name: "Ana" }, last_message: { text: "Ya te lo aparto", direction: "outbound" } }),
    conv(3, { contact: { id: "k3", name: null, phone: "+584123334444" }, last_message: null }),
  ]);
  renderInbox();
  const maria = await screen.findByRole("link", { name: /María Pérez/ });
  const list = screen.getByRole("list", { name: "Lista de conversaciones" });
  expect(maria).toHaveTextContent("Hola, soy el cliente 1");
  expect(maria).toHaveTextContent("Espera respuesta");
  const cliente2 = within(list).getByRole("link", { name: /Cliente 2/ });
  expect(cliente2).toHaveTextContent("Tú: Ya te lo aparto");
  expect(cliente2).toHaveTextContent("Persona");
  expect(cliente2).toHaveTextContent("Asignada a Ana");
  const sinNombre = within(list).getByRole("link", { name: /\+58 412 3334444/ });
  expect(sinNombre).toHaveTextContent("Sin mensajes todavía");
});

test("filtrar por quién atiende pide solo esas conversaciones", async () => {
  const urls = fakeApi([conv(1), conv(2, { mode: "human" }), conv(3)]);
  renderInbox();
  await screen.findByRole("link", { name: /Cliente 1/ });
  await userEvent.setup().selectOptions(screen.getByLabelText("Quién atiende"), "human");
  await waitFor(() => expect(screen.queryByRole("link", { name: /Cliente 1/ })).not.toBeInTheDocument());
  expect(screen.getByRole("link", { name: /Cliente 2/ })).toBeInTheDocument();
  expect(inboxCalls(urls).some((u) => u.includes("mode=human"))).toBe(true);
});

test("las cerradas están aparte", async () => {
  fakeApi([conv(1), conv(2, { status: "closed" })]);
  renderInbox();
  const user = userEvent.setup();
  await screen.findByRole("link", { name: /Cliente 1/ });
  expect(screen.queryByRole("link", { name: /Cliente 2/ })).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Cerradas" }));
  expect(await screen.findByRole("link", { name: /Cliente 2/ })).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: /Cliente 1/ })).not.toBeInTheDocument();
});

test("la búsqueda espera a que termines de escribir y se puede quitar", async () => {
  const urls = fakeApi([conv(1, { contact: { id: "k1", name: "Ana López", phone: null } }), conv(2)]);
  renderInbox();
  const user = userEvent.setup();
  await screen.findByRole("link", { name: /Ana López/ });
  await user.type(screen.getByLabelText("Buscar por nombre o teléfono"), "ana");
  await waitFor(() => expect(screen.queryByRole("link", { name: /Cliente 2/ })).not.toBeInTheDocument());
  // Una sola búsqueda con el texto completo, no una por letra.
  const searches = inboxCalls(urls).filter((u) => u.includes("q="));
  expect(searches).toHaveLength(1);
  expect(searches[0]).toContain("q=ana");

  await user.clear(screen.getByLabelText("Buscar por nombre o teléfono"));
  await user.type(screen.getByLabelText("Buscar por nombre o teléfono"), "zzz");
  expect(await screen.findByText("Ninguna conversación coincide")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Quitar filtros" }));
  expect(await screen.findByRole("link", { name: /Cliente 2/ })).toBeInTheDocument();
  expect(screen.getByLabelText("Buscar por nombre o teléfono")).toHaveValue("");
});

test("sin conversaciones explica qué pasará", async () => {
  fakeApi([]);
  renderInbox();
  expect(await screen.findByText("Todavía no hay conversaciones")).toBeInTheDocument();
});

test("ver más antiguas pide la página siguiente desde la última que se ve", async () => {
  const rows = Array.from({ length: 35 }, (_, i) => conv(i + 1));
  const urls = fakeApi(rows);
  renderInbox();
  const user = userEvent.setup();
  const rowsShown = async () => within(await screen.findByRole("list", { name: "Lista de conversaciones" })).getAllByRole("listitem");
  await waitFor(async () => expect(await rowsShown()).toHaveLength(30));
  await user.click(screen.getByRole("button", { name: "Ver más antiguas" }));
  await waitFor(async () => expect(await rowsShown()).toHaveLength(35));
  const next = inboxCalls(urls).find((u) => u.includes("before="))!;
  expect(decodeURIComponent(next)).toContain(`before=${rows[29].last_message_at}`);
  expect(screen.queryByRole("button", { name: "Ver más antiguas" })).not.toBeInTheDocument();
});

test("abrir una conversación muestra quién es y se puede volver a la lista", async () => {
  const urls = fakeApi([conv(1, { contact: { id: "k1", name: "María Pérez", phone: "+584121111111" } }), conv(2)]);
  renderInbox();
  const user = userEvent.setup();
  await user.click(await screen.findByRole("link", { name: /María Pérez/ }));
  const pane = await screen.findByRole("region", { name: "Conversación" });
  expect(await within(pane).findByRole("heading", { name: "María Pérez" })).toBeInTheDocument();
  expect(within(pane).getByText(/\+58 412 1111111/)).toBeInTheDocument();
  expect(urls).toContain("/conversations/c1");
  expect(screen.getByRole("link", { name: /María Pérez/ })).toHaveAttribute("aria-current", "true");
  await user.click(within(pane).getByRole("link", { name: /Volver a la lista/ }));
  await waitFor(() => expect(screen.queryByRole("region", { name: "Conversación" })).not.toBeInTheDocument());
});

// --- Piezas puras ---

test("los filtros viajan en la dirección", () => {
  const filters = filtersFromParams(new URLSearchParams("status=closed&mode=human&mine=1&number=n1&q=%20ana%20"));
  expect(filters).toEqual({ status: "closed", mode: "human", mine: true, number_id: "n1", q: "ana" });
  expect(filtersFromParams(new URLSearchParams("mode=otro"))).toMatchObject({ status: "open", mode: undefined });
  expect(hasActiveFilters(filtersFromParams(new URLSearchParams("")))).toBe(false);
  expect(hasActiveFilters(filtersFromParams(new URLSearchParams("mine=1")))).toBe(true);
});

test("un aviso en tiempo real no borra las páginas ya abiertas", () => {
  const [c1, c2, c3, c4, c5, c6] = Array.from({ length: 6 }, (_, i) => conv(i + 1));
  const nueva = conv(0); // llegó un mensaje: ahora es la más reciente

  // Se habían abierto las 6. La primera página (de 3) trae la nueva arriba: lo más antiguo se conserva.
  expect(mergeFirstPage([c1, c2, c3, c4, c5, c6], [nueva, c1, c2], 3).map((c) => c.id)).toEqual(["c0", "c1", "c2", "c3", "c4", "c5", "c6"]);
  // Si la primera página no está llena, es todo lo que hay.
  expect(mergeFirstPage([c1, c2, c3], [c1], 3)).toEqual([c1]);
  // c2 ya no cumple el filtro (la cerraron): no viene y es más nueva que la última de la página, se quita.
  expect(mergeFirstPage([c1, c2, c3], [c1, c3, c4], 3).map((c) => c.id)).toEqual(["c1", "c3", "c4"]);
});

test("nombre y vista previa", () => {
  expect(contactName(conv(1, { contact: { id: "k", name: "  ", phone: "+584121234567" } }))).toBe("+58 412 1234567");
  expect(contactName(conv(1, { contact: { id: "k", name: null, phone: null } }))).toBe("Contacto sin nombre");
  expect(previewText(conv(1, { last_message: { text: null, direction: "inbound" } }))).toContain("Mensaje sin texto");
  expect(previewText(conv(1, { last_message: { text: "a\n\nb", direction: "outbound" } }))).toBe("Tú: a b");
});
