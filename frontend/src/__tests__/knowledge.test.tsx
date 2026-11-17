import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

import { App } from "../App";
import { storeSession } from "../api/client";
import type { KnowledgeSource } from "../api/types";
import { SessionProvider } from "../auth/session";
import { ToastProvider } from "../components/Toast";
import { canRetry, isStuck, readTextFile, sizeLine, titleFromFilename } from "../lib/knowledge";
import { knowledgeTiming } from "../pages/Knowledge";

function json(status: number, body: unknown): Response {
  return new Response(status === 204 ? null : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function source(id: string, title: string, overrides: Partial<KnowledgeSource> = {}): KnowledgeSource {
  return {
    id,
    title,
    source_type: "text",
    status: "ready",
    error: null,
    chunk_count: 3,
    characters: 900,
    created_at: "2026-11-10T10:00:00Z",
    updated_at: new Date().toISOString(),
    created_by: "Andre Pérez",
    can_edit: true,
    ...overrides,
  };
}

interface World {
  sources: KnowledgeSource[];
  calls: { method: string; url: string; body: Record<string, unknown> | undefined }[];
}

function fakeApi(role: string, w: World) {
  const tenant = { id: "t1", name: "Panadería Prueba", slug: "panaderia", role };
  const session = {
    access_token: "pase",
    expires_at: new Date(Date.now() + 900_000).toISOString(),
    user: { id: "u1", email: "andre@saint.ai", full_name: "Andre Pérez" },
    tenants: [tenant],
    active_tenant: tenant,
  };
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : undefined;
    w.calls.push({ method, url, body });

    if (url === "/auth/refresh") return json(200, session);
    if (url === "/events") return new Response(null, { status: 503 });
    if (url === "/knowledge" && method === "GET") return json(200, w.sources);
    if (url === "/knowledge" && method === "POST") {
      if (body?.text === "TEXTO YA LISTO") return json(202, { ...w.sources[0], already_loaded: true });
      const created = source(`new${w.sources.length}`, String(body?.title), { status: "processing", chunk_count: 1, characters: String(body?.text).length });
      w.sources.push(created);
      return json(202, { ...created, already_loaded: false });
    }
    const one = /^\/knowledge\/([^/]+)(\/retry)?$/.exec(url);
    if (one) {
      const found = w.sources.find((s) => s.id === one[1])!;
      if (one[2]) {
        found.status = "processing";
        found.error = null;
        return json(202, found);
      }
      if (method === "DELETE") {
        w.sources = w.sources.filter((s) => s.id !== found.id);
        return json(204, null);
      }
      return json(200, {
        ...found,
        chunks: [
          { index: 0, content: "Abrimos de lunes a viernes de 8 a 18.", token_count: 120, has_embedding: true },
          { index: 1, content: "Los domingos cerramos.", token_count: 40, has_embedding: false },
        ],
      });
    }
    if (url === "/hours") return json(200, { days: {}, after_hours_message: null, open_now: true, timezone: "America/Caracas", can_edit: true });
    if (url === "/agent") return json(200, { version: 1 });
    return json(200, []);
  });
}

function baseWorld(): World {
  return {
    sources: [
      source("a", "Horarios y ubicación"),
      source("b", "Lista de precios", { status: "failed", error: "Falta la clave de OpenRouter", chunk_count: 2, characters: 500 }),
      source("c", "Promociones", { status: "processing", chunk_count: 1, characters: 200 }),
    ],
    calls: [],
  };
}

function renderKnowledge() {
  return render(
    <MemoryRouter initialEntries={["/conocimiento"]}>
      <SessionProvider>
        <ToastProvider>
          <App />
        </ToastProvider>
      </SessionProvider>
    </MemoryRouter>,
  );
}

const row = (title: string) => screen.getByText(title).closest("li") as HTMLElement;
const sent = (w: World, method: string, url: string) => w.calls.filter((c) => c.method === method && c.url === url);
const original = { ...knowledgeTiming };

afterEach(() => {
  vi.restoreAllMocks();
  storeSession(null);
  localStorage.clear();
  Object.assign(knowledgeTiming, original);
});

test("lista los documentos con su estado, tamaño y el motivo si fallaron", async () => {
  fakeApi("owner", baseWorld());
  renderKnowledge();
  await screen.findByText("Horarios y ubicación");
  expect(within(row("Horarios y ubicación")).getByText("Listo")).toBeInTheDocument();
  expect(within(row("Horarios y ubicación")).getByText(/3 fragmentos · 900 caracteres · subido por Andre Pérez/)).toBeInTheDocument();
  const failed = row("Lista de precios");
  expect(within(failed).getByText("Falló")).toBeInTheDocument();
  expect(within(failed).getByText(/Falta la clave de OpenRouter/)).toBeInTheDocument();
  expect(within(failed).getByRole("button", { name: "Reintentar Lista de precios" })).toBeInTheDocument();
  expect(within(row("Promociones")).getByText("Procesando")).toBeInTheDocument();
  expect(within(row("Promociones")).queryByRole("button", { name: /Reintentar/ })).not.toBeInTheDocument();
  expect(screen.getByText("El agente todavía no usa este conocimiento")).toBeInTheDocument();
});

test("un agente solo puede mirar", async () => {
  fakeApi("agent", baseWorld());
  renderKnowledge();
  await screen.findByText("Horarios y ubicación");
  expect(screen.getByText("Solo lectura")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Subir conocimiento" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Reintentar/ })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Borrar/ })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Ver los fragmentos de Horarios y ubicación" })).toBeInTheDocument();
});

test("subir un texto pegado lo deja procesándose", async () => {
  const w = baseWorld();
  fakeApi("owner", w);
  renderKnowledge();
  const user = userEvent.setup();
  await screen.findByText("Horarios y ubicación");
  await user.click(screen.getByRole("button", { name: "Subir conocimiento" }));
  const dialog = await screen.findByRole("dialog", { name: "Subir conocimiento" });
  const submit = within(dialog).getByRole("button", { name: "Subir" });
  expect(submit).toBeDisabled();
  await user.type(within(dialog).getByLabelText("Título"), "Menú del día");
  await user.type(within(dialog).getByLabelText("Texto"), "Pan francés: 1 dólar");
  await user.click(submit);

  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  expect(sent(w, "POST", "/knowledge")[0].body).toEqual({ title: "Menú del día", text: "Pan francés: 1 dólar" });
  expect(await screen.findByText("Menú del día")).toBeInTheDocument();
  expect(within(row("Menú del día")).getByText("Procesando")).toBeInTheDocument();
  expect(screen.getAllByRole("status").some((el) => el.textContent?.includes("Se subió"))).toBe(true);
});

test("si el mismo texto ya estaba listo, avisa que no se hizo nada", async () => {
  fakeApi("owner", baseWorld());
  renderKnowledge();
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: "Subir conocimiento" }));
  const dialog = await screen.findByRole("dialog");
  await user.type(within(dialog).getByLabelText("Título"), "Otro título");
  await user.type(within(dialog).getByLabelText("Texto"), "TEXTO YA LISTO");
  await user.click(within(dialog).getByRole("button", { name: "Subir" }));
  await waitFor(() => expect(screen.getAllByRole("status").some((el) => el.textContent?.includes("ya estaba cargado"))).toBe(true));
});

test("cargar un archivo .md rellena el texto y sugiere el título", async () => {
  fakeApi("owner", baseWorld());
  renderKnowledge();
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: "Subir conocimiento" }));
  const dialog = await screen.findByRole("dialog");
  const file = new File(["# Horarios\nLunes a viernes de 8 a 18"], "horarios_de_la_tienda.md", { type: "text/markdown" });
  await user.upload(within(dialog).getByLabelText("Elegir un archivo de texto"), file);
  await waitFor(() => expect(within(dialog).getByLabelText("Texto")).toHaveValue("# Horarios\nLunes a viernes de 8 a 18"));
  expect(within(dialog).getByLabelText("Título")).toHaveValue("horarios de la tienda");
});

test("un archivo que no es de texto se rechaza con una explicación", async () => {
  fakeApi("owner", baseWorld());
  renderKnowledge();
  const user = userEvent.setup({ applyAccept: false });
  await user.click(await screen.findByRole("button", { name: "Subir conocimiento" }));
  const dialog = await screen.findByRole("dialog");
  await user.upload(within(dialog).getByLabelText("Elegir un archivo de texto"), new File(["x"], "lista.pdf", { type: "application/pdf" }));
  expect(await within(dialog).findByText("Solo se pueden cargar archivos .txt o .md.")).toBeInTheDocument();
  expect(within(dialog).getByLabelText("Texto")).toHaveValue("");
});

test("reintentar uno que falló lo vuelve a procesar", async () => {
  const w = baseWorld();
  fakeApi("owner", w);
  renderKnowledge();
  const user = userEvent.setup();
  await screen.findByText("Lista de precios");
  await user.click(screen.getByRole("button", { name: "Reintentar Lista de precios" }));
  await waitFor(() => expect(within(row("Lista de precios")).getByText("Procesando")).toBeInTheDocument());
  expect(sent(w, "POST", "/knowledge/b/retry")).toHaveLength(1);
  expect(within(row("Lista de precios")).queryByText(/Falta la clave/)).not.toBeInTheDocument();
});

test("borrar pide confirmación y quita el documento", async () => {
  const w = baseWorld();
  fakeApi("owner", w);
  renderKnowledge();
  const user = userEvent.setup();
  await screen.findByText("Lista de precios");
  await user.click(screen.getByRole("button", { name: "Borrar Lista de precios" }));
  const dialog = await screen.findByRole("dialog", { name: "Borrar el documento" });
  expect(sent(w, "DELETE", "/knowledge/b")).toHaveLength(0);
  await user.click(within(dialog).getByRole("button", { name: "Borrar documento" }));
  await waitFor(() => expect(screen.queryByText("Lista de precios")).not.toBeInTheDocument());
  expect(sent(w, "DELETE", "/knowledge/b")).toHaveLength(1);
});

test("ver los fragmentos de un documento", async () => {
  fakeApi("agent", baseWorld());
  renderKnowledge();
  const user = userEvent.setup();
  await screen.findByText("Horarios y ubicación");
  await user.click(screen.getByRole("button", { name: "Ver los fragmentos de Horarios y ubicación" }));
  const dialog = await screen.findByRole("dialog", { name: "Horarios y ubicación" });
  expect(await within(dialog).findByText("Abrimos de lunes a viernes de 8 a 18.")).toBeInTheDocument();
  expect(within(dialog).getByText(/120 tokens · listo para buscar/)).toBeInTheDocument();
  expect(within(dialog).getByText(/todavía sin procesar/)).toBeInTheDocument();
});

test("mientras algo se procesa, la lista se pone al día sola", async () => {
  knowledgeTiming.poll = 40;
  const w = baseWorld();
  fakeApi("owner", w);
  renderKnowledge();
  await screen.findByText("Promociones");
  expect(within(row("Promociones")).getByText("Procesando")).toBeInTheDocument();
  w.sources[2].status = "ready"; // el worker terminó
  await waitFor(() => expect(within(row("Promociones")).getByText("Listo")).toBeInTheDocument());
});

test("sin documentos explica qué subir", async () => {
  fakeApi("owner", { sources: [], calls: [] });
  renderKnowledge();
  expect(await screen.findByText("Todavía no subes nada")).toBeInTheDocument();
});

// --- Piezas puras ---

test("el título sugerido sale del nombre del archivo", () => {
  expect(titleFromFilename("horarios_de_la_tienda.md")).toBe("horarios de la tienda");
  expect(titleFromFilename("Precios-2026.txt")).toBe("Precios 2026");
  expect(titleFromFilename("sin_extension")).toBe("sin extension");
});

test("un documento procesándose mucho rato se puede reintentar", () => {
  const now = Date.parse("2026-11-16T12:00:00Z");
  expect(isStuck({ status: "processing", updated_at: "2026-11-16T11:58:00Z" }, now)).toBe(false);
  expect(isStuck({ status: "processing", updated_at: "2026-11-16T11:50:00Z" }, now)).toBe(true);
  expect(isStuck({ status: "ready", updated_at: "2026-11-16T10:00:00Z" }, now)).toBe(false);
  expect(canRetry({ status: "failed", updated_at: "2026-11-16T11:59:00Z" }, now)).toBe(true);
  expect(canRetry({ status: "ready", updated_at: "2026-11-16T11:59:00Z" }, now)).toBe(false);
  expect(sizeLine({ chunk_count: 1, characters: 80 })).toBe("1 fragmento · 80 caracteres");
});

test("leer archivos: vacío y demasiado pesado", async () => {
  await expect(readTextFile(new File(["  "], "vacio.txt"))).rejects.toThrow("El archivo está vacío.");
  await expect(readTextFile(new File(["x"], "foto.png"))).rejects.toThrow("Solo se pueden cargar archivos .txt o .md.");
  const big = new File([new Uint8Array(1_000_001)], "grande.txt");
  await expect(readTextFile(big)).rejects.toThrow("pesa más de 1 MB");
});
