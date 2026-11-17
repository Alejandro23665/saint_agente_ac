import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

import { App } from "../App";
import { storeSession } from "../api/client";
import type { Invitation, Member, Role } from "../api/types";
import { SessionProvider } from "../auth/session";
import { ToastProvider } from "../components/Toast";
import { canChangeRoles, canManage, invitableRoles, needsConfirmation, ownerCount } from "../lib/permissions";

const LINK = "http://127.0.0.1:5173/invitacion#TOKENDEPRUEBA1234567890";

function json(status: number, body: unknown): Response {
  return new Response(status === 204 ? null : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function member(id: string, name: string, role: Role, overrides: Partial<Member> = {}): Member {
  return {
    user_id: id,
    email: `${name.split(" ")[0].toLowerCase()}@saint.ai`,
    full_name: name,
    role,
    joined_at: "2026-02-01T10:00:00Z",
    last_login_at: "2026-11-15T10:00:00Z",
    is_me: false,
    ...overrides,
  };
}

function invitation(id: string, email: string, overrides: Partial<Invitation> = {}): Invitation {
  return {
    id,
    email,
    role: "agent",
    expires_at: "2099-01-01T10:00:00Z",
    created_at: "2026-11-15T10:00:00Z",
    expired: false,
    ...overrides,
  };
}

interface World {
  myRole: Role;
  members: Member[];
  invitations: Invitation[];
  tenants: { id: string; name: string; slug: string; role: string }[];
  inviteError?: string;
  calls: { method: string; url: string; body: Record<string, unknown> | undefined }[];
}

function world(myRole: Role, others: Member[] = []): World {
  return {
    myRole,
    members: [member("u1", "Andre Pérez", myRole, { is_me: true }), ...others],
    invitations: [],
    tenants: [{ id: "t1", name: "Panadería Prueba", slug: "panaderia", role: myRole }],
    calls: [],
  };
}

function fakeApi(w: World) {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : undefined;
    w.calls.push({ method, url, body });

    if (url === "/auth/refresh") {
      const wanted = body?.tenant_id;
      const active = w.tenants.find((t) => t.id === wanted) ?? (w.tenants.length === 1 ? w.tenants[0] : null);
      return json(200, {
        access_token: "pase",
        expires_at: new Date(Date.now() + 900_000).toISOString(),
        user: { id: "u1", email: "andre@saint.ai", full_name: "Andre Pérez" },
        tenants: w.tenants,
        active_tenant: active,
      });
    }
    if (url === "/events") return new Response(null, { status: 503 });
    if (url === "/tenant/members") return json(200, w.members);
    if (url === "/tenant/invitations" && method === "GET") return json(200, w.invitations);
    if (url === "/tenant/invitations" && method === "POST") {
      if (w.inviteError) return json(409, { detail: w.inviteError });
      const created = invitation(`i${w.invitations.length + 1}`, String(body?.email), { role: body?.role as Role });
      w.invitations.unshift(created);
      return json(201, { ...created, link: LINK });
    }
    const inv = /^\/tenant\/invitations\/([^/]+)$/.exec(url);
    if (inv && method === "DELETE") {
      w.invitations = w.invitations.filter((i) => i.id !== inv[1]);
      return json(204, null);
    }
    const person = /^\/tenant\/members\/([^/]+)$/.exec(url);
    if (person && method === "PATCH") {
      w.members = w.members.map((m) => (m.user_id === person[1] ? { ...m, role: body!.role as Role } : m));
      return json(204, null);
    }
    if (person && method === "DELETE") {
      w.members = w.members.filter((m) => m.user_id !== person[1]);
      if (person[1] === "u1") w.tenants = [{ id: "t2", name: "Taller Rápido", slug: "taller", role: "agent" }];
      return json(204, null);
    }
    if (url === "/hours") return json(200, { days: {}, after_hours_message: null, open_now: true, timezone: "America/Caracas", can_edit: true });
    if (url === "/agent") return json(200, { version: 1 });
    return json(200, []);
  });
}

function renderTeam() {
  localStorage.setItem("saint.negocio", "t1");
  return render(
    <MemoryRouter initialEntries={["/equipo"]}>
      <SessionProvider>
        <ToastProvider>
          <App />
        </ToastProvider>
      </SessionProvider>
    </MemoryRouter>,
  );
}

// Solo dentro de las listas del equipo: el nombre también sale en el menú lateral.
const row = (name: string) => within(screen.getByRole("main")).getByText(name).closest("li") as HTMLElement;
const sent = (w: World, method: string, url: string) => w.calls.filter((c) => c.method === method && c.url === url);

afterEach(() => {
  vi.restoreAllMocks();
  storeSession(null);
  localStorage.clear();
});

const ana = () => member("u2", "Ana Rivas", "agent");
const luis = () => member("u3", "Luis Mora", "admin");
const carla = () => member("u4", "Carla Ruiz", "owner");

test("el dueño ve al equipo y cambia el rol de un agente", async () => {
  const w = world("owner", [ana(), luis()]);
  fakeApi(w);
  renderTeam();
  const user = userEvent.setup();
  await screen.findByText("Ana Rivas");
  expect(within(row("Andre Pérez")).getByText("(tú)")).toBeInTheDocument();
  expect(within(row("Ana Rivas")).getByText("ana@saint.ai")).toBeInTheDocument();

  await user.selectOptions(screen.getByRole("combobox", { name: "Rol de Ana Rivas" }), "admin");
  await waitFor(() => expect(sent(w, "PATCH", "/tenant/members/u2")).toHaveLength(1));
  expect(sent(w, "PATCH", "/tenant/members/u2")[0].body).toEqual({ role: "admin" });
  await waitFor(() => expect(screen.getByRole("combobox", { name: "Rol de Ana Rivas" })).toHaveValue("admin"));
});

test("dar o quitar el rol de dueño pide confirmación", async () => {
  const w = world("owner", [ana(), carla()]);
  fakeApi(w);
  renderTeam();
  const user = userEvent.setup();
  await screen.findByText("Ana Rivas");

  await user.selectOptions(screen.getByRole("combobox", { name: "Rol de Ana Rivas" }), "owner");
  const dialog = await screen.findByRole("dialog", { name: "Cambiar el rol" });
  expect(within(dialog).getByText(/tendrá control total del negocio/)).toBeInTheDocument();
  expect(sent(w, "PATCH", "/tenant/members/u2")).toHaveLength(0);
  await user.click(within(dialog).getByRole("button", { name: "Cambiar rol" }));
  await waitFor(() => expect(sent(w, "PATCH", "/tenant/members/u2")[0].body).toEqual({ role: "owner" }));

  await user.selectOptions(screen.getByRole("combobox", { name: "Rol de Carla Ruiz" }), "agent");
  expect(await screen.findByText(/dejará de ser dueño/)).toBeInTheDocument();
});

test("un administrador solo gestiona agentes y no cambia roles", async () => {
  fakeApi(world("admin", [ana(), luis(), carla()]));
  renderTeam();
  await screen.findByText("Ana Rivas");
  expect(screen.queryByRole("combobox", { name: /Rol de/ })).not.toBeInTheDocument();
  expect(within(row("Ana Rivas")).getByRole("button", { name: "Quitar a Ana Rivas" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Quitar a Luis Mora" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Quitar a Carla Ruiz" })).not.toBeInTheDocument();
  expect(within(row("Carla Ruiz")).getByText("Dueño")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Invitar a alguien" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Invitaciones pendientes" })).toBeInTheDocument();
});

test("un agente solo mira y puede salir", async () => {
  fakeApi(world("agent", [carla()]));
  renderTeam();
  await screen.findByText("Carla Ruiz");
  expect(screen.queryByRole("button", { name: "Invitar a alguien" })).not.toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "Invitaciones pendientes" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Quitar a/ })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Salir del negocio" })).toBeEnabled();
});

test("quitar a alguien pide confirmación y la saca de la lista", async () => {
  const w = world("owner", [ana()]);
  fakeApi(w);
  renderTeam();
  const user = userEvent.setup();
  await screen.findByText("Ana Rivas");
  await user.click(screen.getByRole("button", { name: "Quitar a Ana Rivas" }));
  const dialog = await screen.findByRole("dialog", { name: "Quitar del equipo" });
  expect(sent(w, "DELETE", "/tenant/members/u2")).toHaveLength(0);
  await user.click(within(dialog).getByRole("button", { name: "Quitar" }));
  await waitFor(() => expect(screen.queryByText("Ana Rivas")).not.toBeInTheDocument());
  expect(sent(w, "DELETE", "/tenant/members/u2")).toHaveLength(1);
});

test("invitar: valida el correo, muestra el enlace una vez y se puede copiar", async () => {
  const w = world("owner", [ana()]);
  fakeApi(w);
  const user = userEvent.setup();
  renderTeam();
  await user.click(await screen.findByRole("button", { name: "Invitar a alguien" }));
  const dialog = await screen.findByRole("dialog", { name: "Invitar a alguien" });

  await user.type(within(dialog).getByLabelText("Correo"), "luis@saint");
  await user.click(within(dialog).getByRole("button", { name: "Crear enlace" }));
  expect(within(dialog).getByText(/Escribe un correo completo/)).toBeInTheDocument();
  expect(sent(w, "POST", "/tenant/invitations")).toHaveLength(0);

  await user.clear(within(dialog).getByLabelText("Correo"));
  await user.type(within(dialog).getByLabelText("Correo"), "luis@saint.ai");
  await user.selectOptions(within(dialog).getByLabelText("Rol"), "admin");
  await user.click(within(dialog).getByRole("button", { name: "Crear enlace" }));

  const ready = await screen.findByRole("dialog", { name: "Invitación lista" });
  expect(sent(w, "POST", "/tenant/invitations")[0].body).toEqual({ email: "luis@saint.ai", role: "admin" });
  expect(within(ready).getByLabelText("Enlace de invitación")).toHaveValue(LINK);
  expect(within(ready).getByText("Este enlace se muestra solo ahora")).toBeInTheDocument();
  await user.click(within(ready).getByRole("button", { name: "Copiar" }));
  expect(await within(ready).findByRole("button", { name: "Copiado" })).toBeInTheDocument();
  expect(await navigator.clipboard.readText()).toBe(LINK);

  await user.click(within(ready).getByRole("button", { name: "Listo, ya lo copié" }));
  const pending = await screen.findByRole("region", { name: "Invitaciones pendientes" });
  expect(await within(pending).findByText("luis@saint.ai")).toBeInTheDocument();
  // El enlace no se vuelve a mostrar en ningún lado.
  expect(screen.queryByDisplayValue(LINK)).not.toBeInTheDocument();
});

test("un administrador solo puede invitar agentes", async () => {
  fakeApi(world("admin"));
  renderTeam();
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: "Invitar a alguien" }));
  const dialog = await screen.findByRole("dialog", { name: "Invitar a alguien" });
  const role = within(dialog).getByLabelText("Rol");
  expect(role).toBeDisabled();
  expect(within(role).getAllByRole("option")).toHaveLength(1);
  expect(within(dialog).getByText(/solo puedes invitar agentes/)).toBeInTheDocument();
});

test("si la API rechaza la invitación, el motivo se ve en la ventana", async () => {
  const w = world("owner", [ana()]);
  w.inviteError = "Esa persona ya es miembro del negocio";
  fakeApi(w);
  renderTeam();
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: "Invitar a alguien" }));
  const dialog = await screen.findByRole("dialog");
  await user.type(within(dialog).getByLabelText("Correo"), "ana@saint.ai");
  await user.click(within(dialog).getByRole("button", { name: "Crear enlace" }));
  expect(await within(dialog).findByRole("alert")).toHaveTextContent("Esa persona ya es miembro del negocio");
});

test("anular una invitación vencida", async () => {
  const w = world("owner");
  w.invitations = [invitation("i1", "vieja@saint.ai", { expired: true, expires_at: "2026-11-01T10:00:00Z" }), invitation("i2", "nueva@saint.ai", { role: "admin" })];
  fakeApi(w);
  renderTeam();
  const user = userEvent.setup();
  await screen.findByText("vieja@saint.ai");
  expect(within(row("vieja@saint.ai")).getByText("Venció")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Anular la invitación de vieja@saint.ai" }));
  const dialog = await screen.findByRole("dialog", { name: "Anular la invitación" });
  await user.click(within(dialog).getByRole("button", { name: "Anular invitación" }));
  await waitFor(() => expect(screen.queryByText("vieja@saint.ai")).not.toBeInTheDocument());
  expect(sent(w, "DELETE", "/tenant/invitations/i1")).toHaveLength(1);
  expect(screen.getByText("nueva@saint.ai")).toBeInTheDocument();
});

test("el único dueño no puede salir hasta nombrar a otro", async () => {
  const solo = world("owner", [ana()]);
  fakeApi(solo);
  renderTeam();
  await screen.findByText("Ana Rivas");
  expect(screen.getByRole("button", { name: "Salir del negocio" })).toBeDisabled();
  expect(screen.getByText(/Eres el único dueño/)).toBeInTheDocument();
});

test("con otro dueño, salir del negocio te lleva a tu otro negocio", async () => {
  const w = world("owner", [carla()]);
  fakeApi(w);
  renderTeam();
  const user = userEvent.setup();
  await screen.findByText("Carla Ruiz");
  await user.click(screen.getByRole("button", { name: "Salir del negocio" }));
  const dialog = await screen.findByRole("dialog", { name: "Salir del negocio" });
  await user.click(within(dialog).getByRole("button", { name: "Salir del negocio" }));
  expect(await screen.findByText(/Así va Taller Rápido hoy/)).toBeInTheDocument();
  expect(sent(w, "DELETE", "/tenant/members/u1")).toHaveLength(1);
});

test("la tabla explica qué puede hacer cada rol", async () => {
  fakeApi(world("owner"));
  renderTeam();
  const table = await screen.findByRole("table");
  expect(within(table).getByRole("columnheader", { name: "Administrador" })).toBeInTheDocument();
  expect(within(table).getByRole("row", { name: /Borrar un número/ })).toHaveTextContent("Sí");
  expect(within(table).getByText("Solo agentes")).toBeInTheDocument();
});

// --- Piezas puras ---

test("los permisos de cada rol son los del servidor", () => {
  expect(canManage("owner", "owner")).toBe(true);
  expect(canManage("admin", "agent")).toBe(true);
  expect(canManage("admin", "admin")).toBe(false);
  expect(canManage("admin", "owner")).toBe(false);
  expect(canManage("agent", "agent")).toBe(false);
  expect(canManage(null, "agent")).toBe(false);
  expect(invitableRoles("owner")).toEqual(["agent", "admin", "owner"]);
  expect(invitableRoles("admin")).toEqual(["agent"]);
  expect(invitableRoles("agent")).toEqual([]);
  expect(canChangeRoles("owner")).toBe(true);
  expect(canChangeRoles("admin")).toBe(false);
  expect(needsConfirmation("agent", "owner")).toBe(true);
  expect(needsConfirmation("owner", "admin")).toBe(true);
  expect(needsConfirmation("agent", "admin")).toBe(false);
  expect(ownerCount([{ role: "owner" }, { role: "agent" }, { role: "owner" }])).toBe(2);
});
