import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

import { App } from "../App";
import { storeSession } from "../api/client";
import type { Agent, AgentVersion, DayKey, Hours } from "../api/types";
import { SessionProvider } from "../auth/session";
import { ToastProvider } from "../components/Toast";
import { allClosed, copyDay, fromDraft, normalizeMessage, toDraft, validateDay, validateDraft, weekdayDraft } from "../lib/hours";

const LONG = "Eres el asistente de la Panadería Prueba. Responde con amabilidad y brevedad.";

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

interface World {
  agent: Agent;
  versions: AgentVersion[];
  hours: Hours;
  calls: { method: string; url: string; body: Record<string, unknown> | undefined }[];
}

function world(canEdit = true): World {
  return {
    agent: { name: "Asistente", instructions: LONG, model: "claude-haiku-5-5", version: 1, updated_at: "2026-11-10T10:00:00Z", can_edit: canEdit },
    versions: [{ version: 1, instructions: LONG, created_at: "2026-11-10T10:00:00Z", created_by: null, is_current: true }],
    hours: { days: {}, after_hours_message: null, timezone: "America/Caracas", open_now: true, can_edit: canEdit },
    calls: [],
  };
}

function bump(w: World, instructions: string, by = "Andre Pérez") {
  const version = w.agent.version + 1;
  w.versions = [
    { version, instructions, created_at: "2026-11-16T12:00:00Z", created_by: by, is_current: true },
    ...w.versions.map((v) => ({ ...v, is_current: false })),
  ];
  w.agent = { ...w.agent, instructions, version, updated_at: "2026-11-16T12:00:00Z" };
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
    if (url === "/agent" && method === "GET") return json(200, w.agent);
    if (url === "/agent" && method === "PATCH") {
      if (typeof body?.name === "string") w.agent = { ...w.agent, name: body.name };
      if (typeof body?.instructions === "string" && body.instructions !== w.agent.instructions) bump(w, body.instructions);
      return json(200, w.agent);
    }
    if (url === "/agent/default-instructions") return json(200, { instructions: "Texto de fábrica del agente para Panadería Prueba." });
    if (url === "/agent/versions") return json(200, w.versions);
    const restore = /^\/agent\/versions\/(\d+)\/restore$/.exec(url);
    if (restore) {
      const old = w.versions.find((v) => v.version === Number(restore[1]))!;
      bump(w, old.instructions);
      return json(200, w.agent);
    }
    if (url === "/hours" && method === "GET") return json(200, w.hours);
    if (url === "/hours" && method === "PUT") {
      w.hours = { ...w.hours, days: (body!.days as Hours["days"]) ?? {}, after_hours_message: (body!.after_hours_message as string | null) ?? null };
      return json(200, w.hours);
    }
    return json(200, []);
  });
}

function renderAgent() {
  return render(
    <MemoryRouter initialEntries={["/agente"]}>
      <SessionProvider>
        <ToastProvider>
          <App />
        </ToastProvider>
      </SessionProvider>
    </MemoryRouter>,
  );
}

const sent = (w: World, method: string, url: string) => w.calls.filter((c) => c.method === method && c.url === url);

afterEach(() => {
  vi.restoreAllMocks();
  storeSession(null);
  localStorage.clear();
});

test("guardar nuevas instrucciones crea una versión y la muestra en el historial", async () => {
  const w = world();
  fakeApi("owner", w);
  renderAgent();
  const user = userEvent.setup();
  const box = await screen.findByLabelText("Instrucciones");
  expect(box).toHaveValue(LONG);
  expect(screen.getByRole("button", { name: "Guardar" })).toBeDisabled();

  await user.clear(box);
  await user.type(box, "Responde siempre en español y con muy buen humor.");
  expect(screen.getByText("Cambios sin guardar")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Guardar" }));

  await waitFor(() => expect(screen.getByText("Versión 2", { selector: ".pill" })).toBeInTheDocument());
  expect(sent(w, "PATCH", "/agent")[0].body).toEqual({ instructions: "Responde siempre en español y con muy buen humor." });
  const history = screen.getByRole("region", { name: "Historial de versiones" });
  expect(await within(history).findByText("Versión 2")).toBeInTheDocument();
  expect(within(history).getByText("Actual")).toBeInTheDocument();
});

test("instrucciones muy cortas no se pueden guardar", async () => {
  fakeApi("owner", world());
  renderAgent();
  const user = userEvent.setup();
  const box = await screen.findByLabelText("Instrucciones");
  await user.clear(box);
  await user.type(box, "Hola");
  expect(screen.getByText(/Escribe al menos 20 caracteres/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Guardar" })).toBeDisabled();
});

test("solo cambiar el nombre no crea una versión", async () => {
  const w = world();
  fakeApi("owner", w);
  renderAgent();
  const user = userEvent.setup();
  const name = await screen.findByLabelText("Nombre del agente");
  await user.clear(name);
  await user.type(name, "Sofía");
  await user.click(screen.getByRole("button", { name: "Guardar" }));
  await waitFor(() => expect(sent(w, "PATCH", "/agent")).toHaveLength(1));
  expect(sent(w, "PATCH", "/agent")[0].body).toEqual({ name: "Sofía" });
  expect(w.agent.version).toBe(1);
});

test("restablecer trae el texto de fábrica al cuadro sin guardarlo", async () => {
  const w = world();
  fakeApi("owner", w);
  renderAgent();
  const user = userEvent.setup();
  const box = await screen.findByLabelText("Instrucciones");
  await user.click(screen.getByRole("button", { name: "Restablecer las de fábrica" }));
  await waitFor(() => expect(box).toHaveValue("Texto de fábrica del agente para Panadería Prueba."));
  expect(sent(w, "PATCH", "/agent")).toHaveLength(0);
  await user.click(within(screen.getByRole("region", { name: "Instrucciones del agente" })).getByRole("button", { name: "Descartar cambios" }));
  expect(box).toHaveValue(LONG);
});

test("un agente solo puede leer: ni guardar ni cambiar el horario", async () => {
  fakeApi("agent", world(false));
  renderAgent();
  const box = await screen.findByLabelText("Instrucciones");
  expect(box).toHaveAttribute("readonly");
  expect(screen.queryByRole("button", { name: "Guardar" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Guardar horario" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Volver a la versión/ })).not.toBeInTheDocument();
  expect(screen.getAllByText("Solo lectura").length).toBeGreaterThan(0);
});

test("ver una versión anterior y volver a ella", async () => {
  const w = world();
  bump(w, "Segunda versión de las instrucciones del agente.");
  fakeApi("owner", w);
  renderAgent();
  const user = userEvent.setup();
  const history = await screen.findByRole("region", { name: "Historial de versiones" });
  await within(history).findByText("Versión 2");

  await user.click(within(history).getByRole("button", { name: "Ver la versión 1" }));
  const view = await screen.findByRole("dialog", { name: "Versión 1" });
  expect(within(view).getByText(LONG)).toBeInTheDocument();
  await user.click(within(view).getByRole("button", { name: "Volver a esta versión" }));

  const confirm = await screen.findByRole("dialog", { name: "Volver a la versión 1" });
  await user.click(within(confirm).getByRole("button", { name: "Volver a esta versión" }));
  await waitFor(() => expect(sent(w, "POST", "/agent/versions/1/restore")).toHaveLength(1));
  await waitFor(() => expect(screen.getByLabelText("Instrucciones")).toHaveValue(LONG));
  expect(await within(history).findByText("Versión 3")).toBeInTheDocument();
});

// --- Horario ---

test("activar el horario parte de lunes a viernes y se guarda con los 7 días", async () => {
  const w = world();
  fakeApi("owner", w);
  renderAgent();
  const user = userEvent.setup();
  const toggle = await screen.findByRole("switch", { name: /Tengo un horario de atención/ });
  expect(toggle).not.toBeChecked();
  expect(screen.queryByLabelText("Lunes, tramo 1, abre")).not.toBeInTheDocument();

  await user.click(toggle);
  expect(screen.getByLabelText("Lunes, tramo 1, abre")).toHaveValue("08:00");
  expect(screen.getByRole("switch", { name: "Sábado abierto" })).not.toBeChecked();

  fireEvent.change(screen.getByLabelText("Lunes, tramo 1, abre"), { target: { value: "09:30" } });
  await user.click(screen.getByRole("switch", { name: "Sábado abierto" }));
  await user.type(screen.getByLabelText("Mensaje fuera de horario"), "Ya cerramos.");
  await user.click(screen.getByRole("button", { name: "Guardar horario" }));

  await waitFor(() => expect(sent(w, "PUT", "/hours")).toHaveLength(1));
  const body = sent(w, "PUT", "/hours")[0].body as { days: Record<DayKey, string[][]>; after_hours_message: string };
  expect(body.days.mon).toEqual([["09:30", "18:00"]]);
  expect(body.days.sat).toEqual([["08:00", "18:00"]]);
  expect(body.days.sun).toEqual([]);
  expect(Object.keys(body.days)).toHaveLength(7);
  expect(body.after_hours_message).toBe("Ya cerramos.");
  expect(await within(screen.getByRole("region", { name: "Horario de atención" })).findByText("Abierto ahora")).toBeInTheDocument();
});

test("un cierre antes de la apertura se avisa y no deja guardar", async () => {
  fakeApi("owner", world());
  renderAgent();
  const user = userEvent.setup();
  await user.click(await screen.findByRole("switch", { name: /Tengo un horario de atención/ }));
  fireEvent.change(screen.getByLabelText("Martes, tramo 1, cierra"), { target: { value: "07:00" } });
  expect(await screen.findByText("El cierre debe ser después de la apertura.")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Guardar horario" })).toBeDisabled();
});

test("copiar un día a todos y quitar el horario (abierto siempre)", async () => {
  const w = world();
  w.hours = { ...w.hours, days: { mon: [["08:00", "12:00"], ["14:00", "18:00"]] } };
  fakeApi("owner", w);
  renderAgent();
  const user = userEvent.setup();
  expect(await screen.findByLabelText("Lunes, tramo 2, cierra")).toHaveValue("18:00");

  const monday = screen.getByLabelText("Lunes, tramo 1, abre").closest("li") as HTMLElement;
  await user.click(within(monday).getByRole("button", { name: "Copiar a todos los días" }));
  expect(screen.getByLabelText("Domingo, tramo 2, abre")).toHaveValue("14:00");
  await user.click(screen.getByRole("button", { name: "Guardar horario" }));
  await waitFor(() => expect(sent(w, "PUT", "/hours")).toHaveLength(1));
  expect((sent(w, "PUT", "/hours")[0].body!.days as Record<string, string[][]>).sun).toEqual([["08:00", "12:00"], ["14:00", "18:00"]]);

  await user.click(screen.getByRole("switch", { name: /Tengo un horario de atención/ }));
  await user.click(screen.getByRole("button", { name: "Guardar horario" }));
  await waitFor(() => expect(sent(w, "PUT", "/hours")).toHaveLength(2));
  expect(sent(w, "PUT", "/hours")[1].body).toEqual({ days: {}, after_hours_message: null });
});

test("avisa que el agente todavía no usa el horario", async () => {
  fakeApi("owner", world());
  renderAgent();
  expect(await screen.findByText("El agente todavía no usa el horario")).toBeInTheDocument();
});

// --- Piezas puras ---

test("el borrador del horario va y viene a lo que guarda la API", () => {
  const draft = toDraft({ tue: [["14:00", "18:00"], ["08:00", "12:00"]], wed: [["08:00", "24:00"]] });
  expect(draft.tue).toEqual([{ start: "08:00", end: "12:00" }, { start: "14:00", end: "18:00" }]);
  expect(draft.wed).toEqual([{ start: "08:00", end: "23:59" }]);
  const days = fromDraft(draft);
  expect(Object.keys(days)).toEqual(["mon", "tue", "wed", "thu", "fri", "sat", "sun"]);
  expect(days.mon).toEqual([]);
  expect(days.tue).toEqual([["08:00", "12:00"], ["14:00", "18:00"]]);
});

test("las reglas del horario son las del servidor", () => {
  expect(validateDay([{ start: "08:00", end: "12:00" }])).toBeNull();
  expect(validateDay([{ start: "12:00", end: "12:00" }])).toBe("El cierre debe ser después de la apertura.");
  expect(validateDay([{ start: "", end: "12:00" }])).toBe("Escribe la hora de apertura y la de cierre.");
  expect(validateDay([{ start: "08:00", end: "12:00" }, { start: "11:00", end: "14:00" }])).toBe("Los tramos se pisan.");
  expect(validateDay([{ start: "08:00", end: "12:00" }, { start: "12:00", end: "14:00" }])).toBeNull();
  const four = ["06:00", "08:00", "10:00", "12:00"].map((start, i) => ({ start, end: `0${7 + i * 2}:30`.slice(-5) }));
  expect(validateDay(four)).toBe("Hasta 3 tramos por día.");
  expect(Object.keys(validateDraft({ ...weekdayDraft(), sat: [{ start: "10:00", end: "09:00" }] }))).toEqual(["sat"]);
});

test("copiar un día, días cerrados y el mensaje", () => {
  const draft = weekdayDraft();
  expect(allClosed(draft)).toBe(false);
  expect(allClosed(toDraft({}))).toBe(true);
  expect(copyDay(draft, "mon").sun).toEqual([{ start: "08:00", end: "18:00" }]);
  expect(normalizeMessage("  Ya   cerramos \n hoy ")).toBe("Ya cerramos hoy");
  expect(normalizeMessage(null)).toBe("");
});
