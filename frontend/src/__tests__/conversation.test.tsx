import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

import { App } from "../App";
import { storeSession } from "../api/client";
import type { Conversation, Message } from "../api/types";
import { SessionProvider } from "../auth/session";
import { ToastProvider } from "../components/Toast";
import { groupByDay, mergeLatest, messageBody, senderLabel } from "../lib/messages";

const ME = { id: "u1", email: "andre@saint.ai", full_name: "Andre Pérez" };

function json(status: number, body: unknown): Response {
  return new Response(status === 204 ? null : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function conversation(overrides: Partial<Conversation> = {}): Conversation {
  return {
    id: "c1",
    status: "open",
    mode: "bot",
    contact: { id: "k1", name: "María Pérez", phone: "+584121111111" },
    number_id: "n1",
    number_phone: "+584121234567",
    assigned: null,
    last_message: { text: "Hola", direction: "inbound" },
    last_message_at: "2026-11-16T12:00:00Z",
    waiting_reply: false,
    created_at: "2026-11-10T10:00:00Z",
    ...overrides,
  };
}

function msg(i: number, overrides: Partial<Message> = {}): Message {
  return {
    id: `m${i}`,
    direction: "inbound",
    sender_type: "contact",
    sender_name: null,
    content_type: "text",
    text: `Mensaje ${i}`,
    transcript: null,
    status: "received",
    error: null,
    created_at: new Date(Date.UTC(2026, 10, 16, 9, 0, 0) + i * 60_000).toISOString(),
    ...overrides,
  };
}

interface World {
  conv: Conversation;
  messages: Message[];
  failReply: boolean;
  calls: { method: string; url: string; body: unknown }[];
}

function world(conv: Conversation, messages: Message[] = []): World {
  return { conv, messages, failReply: false, calls: [] };
}

const MEMBERS = [
  { user_id: "u1", email: "andre@saint.ai", full_name: "Andre Pérez", role: "owner", joined_at: "2026-01-01T00:00:00Z", last_login_at: null, is_me: true },
  { user_id: "u2", email: "ana@saint.ai", full_name: "Ana Rivas", role: "agent", joined_at: "2026-02-01T00:00:00Z", last_login_at: null, is_me: false },
];

function fakeApi(role: string, w: World) {
  const tenant = { id: "t1", name: "Panadería Prueba", slug: "panaderia", role };
  const session = {
    access_token: "pase",
    expires_at: new Date(Date.now() + 900_000).toISOString(),
    user: ME,
    tenants: [tenant],
    active_tenant: tenant,
  };
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    const u = new URL(url, "http://x");
    const method = init?.method ?? "GET";
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    w.calls.push({ method, url, body });

    if (u.pathname === "/auth/refresh") return json(200, session);
    if (u.pathname === "/events") return new Response(null, { status: 503 });
    if (u.pathname === "/tenant/members") return json(200, MEMBERS);
    if (u.pathname === "/conversations" && method === "GET") return json(200, [w.conv]);

    const action = /^\/conversations\/c1(\/messages|\/take|\/release|\/assign|\/close)?$/.exec(u.pathname);
    if (action) {
      const part = action[1];
      if (!part) return json(200, w.conv);
      if (part === "/messages" && method === "GET") {
        const before = u.searchParams.get("before");
        const limit = Number(u.searchParams.get("limit") ?? 50);
        const page = w.messages.filter((m) => !before || Date.parse(m.created_at) < Date.parse(before));
        return json(200, page.slice(-limit));
      }
      if (part === "/messages") {
        if (w.failReply) return json(409, { detail: "La conversación está cerrada" });
        const sent = msg(w.messages.length + 1, {
          direction: "outbound",
          sender_type: "human_panel",
          sender_name: ME.full_name,
          text: body.text,
          status: "queued",
        });
        w.messages.push(sent);
        w.conv = { ...w.conv, mode: "human", assigned: w.conv.assigned ?? { user_id: ME.id, name: ME.full_name } };
        return json(202, { message_id: sent.id });
      }
      if (part === "/take") w.conv = { ...w.conv, mode: "human", assigned: { user_id: ME.id, name: ME.full_name } };
      if (part === "/release") w.conv = { ...w.conv, mode: "bot", assigned: null };
      if (part === "/assign") {
        const member = MEMBERS.find((m) => m.user_id === body.user_id);
        w.conv = { ...w.conv, assigned: member ? { user_id: member.user_id, name: member.full_name } : null };
      }
      if (part === "/close") w.conv = { ...w.conv, status: "closed" };
      return json(204, null);
    }
    if (u.pathname === "/hours") return json(200, { days: {}, after_hours_message: null, open_now: true, timezone: "America/Caracas", can_edit: true });
    if (u.pathname === "/agent") return json(200, { version: 1 });
    return json(200, []);
  });
}

function renderConversation() {
  return render(
    <MemoryRouter initialEntries={["/bandeja/c1"]}>
      <SessionProvider>
        <ToastProvider>
          <App />
        </ToastProvider>
      </SessionProvider>
    </MemoryRouter>,
  );
}

const pane = () => screen.findByRole("region", { name: "Conversación" });
const posted = (w: World, suffix: string) => w.calls.find((c) => c.method === "POST" && c.url === `/conversations/c1${suffix}`);

afterEach(() => {
  vi.restoreAllMocks();
  storeSession(null);
  localStorage.clear();
});

test("la conversación muestra quién escribió cada mensaje y cómo va lo enviado", async () => {
  const w = world(conversation(), [
    msg(1, { text: "Hola, ¿tienen pan?" }),
    msg(2, { direction: "outbound", sender_type: "bot", text: "¡Hola! Sí, tenemos", status: "delivered" }),
    msg(3, { direction: "outbound", sender_type: "human_panel", sender_name: "Ana", text: "Te lo aparto", status: "read" }),
    msg(4, { direction: "outbound", sender_type: "bot", text: "Gracias", status: "failed", error: "El número no tiene WhatsApp" }),
    msg(5, { content_type: "audio", text: null }),
    msg(6, { content_type: "audio", text: null, transcript: "Quiero dos panes" }),
    msg(7, { content_type: "image", text: "mira cómo quedó" }),
  ]);
  fakeApi("owner", w);
  renderConversation();
  const log = await screen.findByRole("log", { name: "Mensajes de la conversación" });
  await within(log).findByText("Hola, ¿tienen pan?");

  expect(within(log).getAllByText("Agente IA")).toHaveLength(2);
  expect(within(log).getByText("Ana (panel)")).toBeInTheDocument();
  expect(within(log).getByText(/Entregado/)).toBeInTheDocument();
  expect(within(log).getByText(/Leído/)).toBeInTheDocument();
  expect(within(log).getByText(/No se envió/)).toBeInTheDocument();
  expect(within(log).getByText("El número no tiene WhatsApp")).toBeInTheDocument();
  expect(within(log).getByText("Todavía no se puede escuchar desde el panel")).toBeInTheDocument();
  expect(within(log).getByText("Quiero dos panes")).toBeInTheDocument();
  expect(within(log).getByText("Transcripción automática")).toBeInTheDocument();
  expect(within(log).getByText("Foto")).toBeInTheDocument();
  expect(within(log).getByText("mira cómo quedó")).toBeInTheDocument();
});

test("contestar: Enter envía, la conversación pasa a persona y el aviso desaparece", async () => {
  const w = world(conversation(), [msg(1, { text: "Hola" })]);
  fakeApi("agent", w);
  renderConversation();
  const user = userEvent.setup();
  const box = await screen.findByRole("textbox", { name: "Escribe tu respuesta" });
  expect(screen.getByText(/el agente IA deja de responder/)).toBeInTheDocument();

  await user.type(box, "Ya te lo aparto{Enter}");
  const log = screen.getByRole("log");
  expect(await within(log).findByText("Ya te lo aparto")).toBeInTheDocument();
  expect(within(log).getByText(/En cola/)).toBeInTheDocument();
  expect(posted(w, "/messages")?.body).toEqual({ text: "Ya te lo aparto" });
  await waitFor(() => expect(box).toHaveValue(""));
  await waitFor(() => expect(screen.queryByText(/el agente IA deja de responder/)).not.toBeInTheDocument());
  expect(within(await pane()).getByText(/La atiendes tú/)).toBeInTheDocument();
});

test("Shift+Enter hace un salto de línea y no envía", async () => {
  const w = world(conversation(), [msg(1)]);
  fakeApi("owner", w);
  renderConversation();
  const user = userEvent.setup();
  const box = await screen.findByRole("textbox", { name: "Escribe tu respuesta" });
  await user.type(box, "uno{Shift>}{Enter}{/Shift}dos");
  expect(box).toHaveValue("uno\ndos");
  expect(posted(w, "/messages")).toBeUndefined();
  expect(screen.getByRole("button", { name: "Enviar" })).toBeEnabled();
});

test("si el envío falla, el texto se queda para reintentar", async () => {
  const w = world(conversation(), [msg(1)]);
  w.failReply = true;
  fakeApi("owner", w);
  renderConversation();
  const user = userEvent.setup();
  const box = await screen.findByRole("textbox", { name: "Escribe tu respuesta" });
  await user.type(box, "Hola María{Enter}");
  await waitFor(() => expect(screen.getAllByRole("status").some((el) => el.textContent?.includes("La conversación está cerrada"))).toBe(true));
  expect(box).toHaveValue("Hola María");
});

test("tomar y devolver al agente", async () => {
  const w = world(conversation(), [msg(1)]);
  fakeApi("agent", w);
  renderConversation();
  const user = userEvent.setup();
  const region = await pane();
  expect(await within(region).findByText(/La atiende el agente IA\./)).toBeInTheDocument();
  expect(within(region).queryByRole("button", { name: "Devolver al agente" })).not.toBeInTheDocument();

  await user.click(within(region).getByRole("button", { name: "Tomar" }));
  expect(await within(region).findByText(/La atiendes tú\./)).toBeInTheDocument();
  expect(posted(w, "/take")).toBeDefined();
  expect(within(region).queryByRole("button", { name: "Tomar" })).not.toBeInTheDocument();

  await user.click(within(region).getByRole("button", { name: "Devolver al agente" }));
  expect(await within(region).findByText(/La atiende el agente IA\./)).toBeInTheDocument();
  expect(posted(w, "/release")).toBeDefined();
});

test("el dueño asigna la conversación a alguien del equipo", async () => {
  const w = world(conversation({ mode: "human" }), [msg(1)]);
  fakeApi("owner", w);
  renderConversation();
  const user = userEvent.setup();
  const region = await pane();
  expect(await within(region).findByText(/La atiende una persona, sin asignar/)).toBeInTheDocument();

  await user.click(within(region).getByRole("button", { name: "Asignar" }));
  const dialog = await screen.findByRole("dialog", { name: "Asignar la conversación" });
  await user.selectOptions(await within(dialog).findByLabelText("Quién la atiende"), "u2");
  await user.click(within(dialog).getByRole("button", { name: "Asignar" }));

  expect(await within(region).findByText(/La atiende Ana Rivas/)).toBeInTheDocument();
  expect(posted(w, "/assign")?.body).toEqual({ user_id: "u2" });
});

test("un agente no ve el botón de asignar", async () => {
  fakeApi("agent", world(conversation(), [msg(1)]));
  renderConversation();
  const region = await pane();
  await within(region).findByRole("button", { name: "Tomar" });
  expect(within(region).queryByRole("button", { name: "Asignar" })).not.toBeInTheDocument();
});

test("cerrar pide confirmación y deja la conversación sin caja para contestar", async () => {
  const w = world(conversation(), [msg(1)]);
  fakeApi("owner", w);
  renderConversation();
  const user = userEvent.setup();
  const region = await pane();
  await user.click(await within(region).findByRole("button", { name: "Cerrar" }));
  const dialog = await screen.findByRole("dialog", { name: "Cerrar la conversación" });
  expect(posted(w, "/close")).toBeUndefined();
  await user.click(within(dialog).getByRole("button", { name: "Cerrar conversación" }));

  expect(await within(region).findByText(/Esta conversación está cerrada\./)).toBeInTheDocument();
  expect(posted(w, "/close")).toBeDefined();
  expect(screen.queryByRole("textbox", { name: "Escribe tu respuesta" })).not.toBeInTheDocument();
  expect(within(region).getByText(/se abre una conversación nueva/)).toBeInTheDocument();
});

test("una conversación cerrada solo se puede leer", async () => {
  fakeApi("owner", world(conversation({ status: "closed" }), [msg(1)]));
  renderConversation();
  const region = await pane();
  await within(region).findByText("Cerrada");
  expect(screen.queryByRole("textbox", { name: "Escribe tu respuesta" })).not.toBeInTheDocument();
  expect(within(region).queryByRole("button", { name: "Tomar" })).not.toBeInTheDocument();
  expect(within(region).queryByRole("button", { name: "Cerrar" })).not.toBeInTheDocument();
});

test("ver mensajes anteriores pide la página de antes del primero que se ve", async () => {
  const all = Array.from({ length: 55 }, (_, i) => msg(i + 1));
  const w = world(conversation(), all);
  fakeApi("owner", w);
  renderConversation();
  const user = userEvent.setup();
  const log = await screen.findByRole("log");
  await within(log).findByText("Mensaje 55");
  expect(within(log).queryByText("Mensaje 5")).not.toBeInTheDocument();

  await user.click(within(log).getByRole("button", { name: "Ver mensajes anteriores" }));
  expect(await within(log).findByText("Mensaje 1")).toBeInTheDocument();
  const older = w.calls.find((c) => c.url.includes("/messages?before="))!;
  expect(decodeURIComponent(older.url)).toContain(`before=${all[5].created_at}`);
  expect(within(log).queryByRole("button", { name: "Ver mensajes anteriores" })).not.toBeInTheDocument();
});

// --- Piezas puras ---

test("un aviso en tiempo real actualiza el estado sin perder los mensajes anteriores", () => {
  const [m1, m2, m3, m4, m5] = [1, 2, 3, 4, 5].map((i) => msg(i));
  const delivered = { ...m4, status: "delivered" as const };
  // Ya estaban abiertos 5; la página más reciente (de 3) trae m4 entregado y m5.
  const merged = mergeLatest([m1, m2, m3, m4, m5], [delivered, m5, msg(6)], 3);
  expect(merged.map((m) => m.id)).toEqual(["m1", "m2", "m3", "m4", "m5", "m6"]);
  expect(merged[3].status).toBe("delivered");
  // Si la página no está llena, es todo lo que hay.
  expect(mergeLatest([m1, m2], [m1, m2, m3], 50)).toEqual([m1, m2, m3]);
});

test("los mensajes se agrupan por día", () => {
  const now = new Date(2026, 10, 16, 15, 0);
  const at = (day: number, hour: number) => new Date(2026, 10, day, hour, 0).toISOString();
  const groups = groupByDay([msg(1, { created_at: at(14, 9) }), msg(2, { created_at: at(15, 20) }), msg(3, { created_at: at(16, 8) }), msg(4, { created_at: at(16, 9) })], now);
  expect(groups.map((g) => [g.label === "Hoy" || g.label === "Ayer" ? g.label : "otro", g.messages.length])).toEqual([
    ["otro", 1],
    ["Ayer", 1],
    ["Hoy", 2],
  ]);
});

test("el contenido de cada tipo de mensaje y quién lo escribió", () => {
  expect(messageBody({ content_type: "text", text: " hola ", transcript: null })).toEqual({ media: null, text: "hola", note: null });
  expect(messageBody({ content_type: "audio", text: null, transcript: null }).note).toContain("Todavía no se puede escuchar");
  expect(messageBody({ content_type: "audio", text: null, transcript: "dos panes" })).toMatchObject({ text: "dos panes", note: "Transcripción automática" });
  expect(messageBody({ content_type: "document", text: null, transcript: null }).media).toBe("Documento");
  expect(messageBody({ content_type: "raro", text: null, transcript: null }).media).toBe("Archivo");
  expect(senderLabel({ sender_type: "bot", sender_name: null })).toBe("Agente IA");
  expect(senderLabel({ sender_type: "human_panel", sender_name: null })).toBe("Una persona (panel)");
  expect(senderLabel({ sender_type: "human_phone", sender_name: null })).toBe("Desde el teléfono del negocio");
  expect(senderLabel({ sender_type: "contact", sender_name: null })).toBeNull();
});
