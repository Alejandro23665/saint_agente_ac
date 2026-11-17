import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

import { App } from "../App";
import { storeSession } from "../api/client";
import { SessionProvider } from "../auth/session";
import { ToastProvider } from "../components/Toast";
import { countryOptions, zoneOptions } from "../lib/regions";

function json(status: number, body: unknown): Response {
  return new Response(status === 204 ? null : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

interface World {
  role: string;
  tenantName: string;
  country: string;
  timezone: string;
  userName: string;
  password: "ok" | "wrong" | "limit";
  closed: number;
  calls: { method: string; url: string; body: Record<string, unknown> | undefined }[];
}

function world(role = "owner"): World {
  return { role, tenantName: "Panadería Prueba", country: "VE", timezone: "America/Caracas", userName: "Andre Pérez", password: "ok", closed: 1, calls: [] };
}

function fakeApi(w: World) {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : undefined;
    w.calls.push({ method, url, body });
    const canEdit = w.role === "owner" || w.role === "admin";

    if (url === "/auth/refresh") {
      const tenant = { id: "t1", name: w.tenantName, slug: "panaderia-prueba", role: w.role };
      return json(200, {
        access_token: "pase",
        expires_at: new Date(Date.now() + 900_000).toISOString(),
        user: { id: "u1", email: "andre@saint.ai", full_name: w.userName },
        tenants: [tenant],
        active_tenant: tenant,
      });
    }
    if (url === "/events") return new Response(null, { status: 503 });
    if (url === "/tenant" && method === "GET") {
      return json(200, {
        id: "t1",
        name: w.tenantName,
        slug: "panaderia-prueba",
        country_code: w.country,
        timezone: w.timezone,
        my_role: w.role,
        can_edit: canEdit,
        can_manage_members: canEdit,
      });
    }
    if (url === "/tenant" && method === "PATCH") {
      if (typeof body?.name === "string") w.tenantName = body.name;
      if (typeof body?.country_code === "string") w.country = body.country_code;
      if (typeof body?.timezone === "string") w.timezone = body.timezone;
      return json(200, {
        id: "t1",
        name: w.tenantName,
        slug: "panaderia-prueba",
        country_code: w.country,
        timezone: w.timezone,
        my_role: w.role,
        can_edit: canEdit,
        can_manage_members: canEdit,
      });
    }
    if (url === "/auth/me" && method === "PATCH") {
      w.userName = String(body?.full_name).split(/\s+/).filter(Boolean).join(" ");
      return json(200, { id: "u1", email: "andre@saint.ai", full_name: w.userName });
    }
    if (url === "/auth/password") {
      if (w.password === "wrong") return json(422, { detail: "La contraseña actual no es correcta" });
      if (w.password === "limit") return json(429, { detail: "Hubo muchos intentos seguidos. Vuelve a probar en unos minutos." });
      return json(200, { closed_sessions: w.closed });
    }
    if (url === "/auth/logout-all" || url === "/auth/logout") return json(204, null);
    if (url === "/hours") return json(200, { days: {}, after_hours_message: null, open_now: true, timezone: w.timezone, can_edit: canEdit });
    if (url === "/agent") return json(200, { version: 1 });
    return json(200, []);
  });
}

function renderSettings() {
  return render(
    <MemoryRouter initialEntries={["/ajustes"]}>
      <SessionProvider>
        <ToastProvider>
          <App />
        </ToastProvider>
      </SessionProvider>
    </MemoryRouter>,
  );
}

const card = (name: string) => screen.findByRole("region", { name });
const sent = (w: World, method: string, url: string) => w.calls.filter((c) => c.method === method && c.url === url);
const toastText = () => screen.getAllByRole("status").map((el) => el.textContent ?? "").join(" | ");

afterEach(() => {
  vi.restoreAllMocks();
  storeSession(null);
  localStorage.clear();
});

test("el dueño cambia el nombre y la zona horaria del negocio", async () => {
  const w = world("owner");
  fakeApi(w);
  renderSettings();
  const user = userEvent.setup();
  const business = await card("Negocio");
  const name = within(business).getByLabelText("Nombre del negocio");
  expect(name).toHaveValue("Panadería Prueba");
  expect(within(business).getByRole("button", { name: "Guardar" })).toBeDisabled();
  expect(within(business).getByText("panaderia-prueba")).toBeInTheDocument();

  await user.clear(name);
  await user.type(name, "Panadería Nueva");
  await user.selectOptions(within(business).getByLabelText("Zona horaria"), "America/Bogota");
  await user.click(within(business).getByRole("button", { name: "Guardar" }));

  await waitFor(() => expect(sent(w, "PATCH", "/tenant")).toHaveLength(1));
  expect(sent(w, "PATCH", "/tenant")[0].body).toEqual({ name: "Panadería Nueva", timezone: "America/Bogota" });
  // El menú se pone al día con el nombre nuevo.
  expect(await screen.findByRole("button", { name: /Negocio: Panadería Nueva/ })).toBeInTheDocument();
  expect(toastText()).toContain("Ajustes guardados");
});

test("un agente ve los datos del negocio pero no los cambia", async () => {
  fakeApi(world("agent"));
  renderSettings();
  const business = await card("Negocio");
  expect(within(business).getByLabelText("Nombre del negocio")).toHaveAttribute("readonly");
  expect(within(business).getByLabelText("País")).toBeDisabled();
  expect(within(business).getByLabelText("Zona horaria")).toBeDisabled();
  expect(within(business).queryByRole("button", { name: "Guardar" })).not.toBeInTheDocument();
  expect(within(business).getByText("Solo lectura")).toBeInTheDocument();
  // Su cuenta sí la puede cambiar.
  expect(within(await card("Mi cuenta")).getByLabelText("Mi nombre")).toBeEnabled();
});

test("cambiar mi nombre actualiza el menú", async () => {
  const w = world("owner");
  fakeApi(w);
  renderSettings();
  const user = userEvent.setup();
  const account = await card("Mi cuenta");
  const field = within(account).getByLabelText("Mi nombre");
  await user.clear(field);
  await user.type(field, "  Andre   Ríos ");
  await user.click(within(account).getByRole("button", { name: "Guardar mi nombre" }));
  await waitFor(() => expect(sent(w, "PATCH", "/auth/me")).toHaveLength(1));
  expect(sent(w, "PATCH", "/auth/me")[0].body).toEqual({ full_name: "  Andre   Ríos " });
  await waitFor(() => expect(screen.getAllByText("Andre Ríos").length).toBeGreaterThan(0));
  expect(within(account).getByLabelText("Correo")).toHaveAttribute("readonly");
});

test("la contraseña nueva se valida antes de llamar a la API", async () => {
  const w = world("owner");
  fakeApi(w);
  renderSettings();
  const user = userEvent.setup();
  const region = await card("Cambiar mi contraseña");
  const button = within(region).getByRole("button", { name: "Cambiar contraseña" });
  await user.type(within(region).getByLabelText("Contraseña actual"), "vieja-clave-1");
  await user.type(within(region).getByLabelText("Contraseña nueva"), "corta");
  await user.click(button);
  expect(within(region).getByText("Usa al menos 10 caracteres.")).toBeInTheDocument();

  await user.clear(within(region).getByLabelText("Contraseña nueva"));
  await user.type(within(region).getByLabelText("Contraseña nueva"), "vieja-clave-1");
  await user.click(button);
  expect(within(region).getByText("La nueva debe ser distinta de la actual.")).toBeInTheDocument();

  await user.clear(within(region).getByLabelText("Contraseña nueva"));
  await user.type(within(region).getByLabelText("Contraseña nueva"), "nueva-clave-2026");
  await user.type(within(region).getByLabelText("Repite la contraseña nueva"), "otra-clave-2026");
  await user.click(button);
  expect(within(region).getByText("Las dos contraseñas no coinciden.")).toBeInTheDocument();
  expect(sent(w, "POST", "/auth/password")).toHaveLength(0);
});

test("cambiar la contraseña avisa cuántas sesiones se cerraron y limpia los campos", async () => {
  const w = world("owner");
  w.closed = 2;
  fakeApi(w);
  renderSettings();
  const user = userEvent.setup();
  const region = await card("Cambiar mi contraseña");
  await user.type(within(region).getByLabelText("Contraseña actual"), "vieja-clave-1");
  await user.type(within(region).getByLabelText("Contraseña nueva"), "nueva-clave-2026");
  await user.type(within(region).getByLabelText("Repite la contraseña nueva"), "nueva-clave-2026");
  await user.click(within(region).getByRole("button", { name: "Cambiar contraseña" }));

  await waitFor(() => expect(sent(w, "POST", "/auth/password")).toHaveLength(1));
  expect(sent(w, "POST", "/auth/password")[0].body).toEqual({ current_password: "vieja-clave-1", new_password: "nueva-clave-2026" });
  await waitFor(() => expect(toastText()).toContain("Se cerró la sesión en 2 dispositivos más"));
  expect(within(region).getByLabelText("Contraseña actual")).toHaveValue("");
  expect(within(region).getByLabelText("Contraseña nueva")).toHaveValue("");
});

test("si la contraseña actual está mal, se dice ahí y la sesión sigue", async () => {
  const w = world("owner");
  w.password = "wrong";
  fakeApi(w);
  renderSettings();
  const user = userEvent.setup();
  const region = await card("Cambiar mi contraseña");
  await user.type(within(region).getByLabelText("Contraseña actual"), "no-es-esta-123");
  await user.type(within(region).getByLabelText("Contraseña nueva"), "nueva-clave-2026");
  await user.type(within(region).getByLabelText("Repite la contraseña nueva"), "nueva-clave-2026");
  await user.click(within(region).getByRole("button", { name: "Cambiar contraseña" }));
  expect(await within(region).findByText("La contraseña actual no es correcta")).toBeInTheDocument();
  // No te saca al inicio de sesión.
  expect(screen.queryByRole("heading", { name: "Inicia sesión en tu panel" })).not.toBeInTheDocument();
  expect(sent(w, "POST", "/auth/refresh").length).toBeLessThanOrEqual(1);
});

test("con demasiados intentos pide esperar", async () => {
  const w = world("owner");
  w.password = "limit";
  fakeApi(w);
  renderSettings();
  const user = userEvent.setup();
  const region = await card("Cambiar mi contraseña");
  await user.type(within(region).getByLabelText("Contraseña actual"), "vieja-clave-1");
  await user.type(within(region).getByLabelText("Contraseña nueva"), "nueva-clave-2026");
  await user.type(within(region).getByLabelText("Repite la contraseña nueva"), "nueva-clave-2026");
  await user.click(within(region).getByRole("button", { name: "Cambiar contraseña" }));
  expect(await within(region).findByText("Espera unos minutos")).toBeInTheDocument();
});

test("cerrar sesión en todos los dispositivos pide confirmación y lleva al inicio de sesión", async () => {
  const w = world("owner");
  fakeApi(w);
  renderSettings();
  const user = userEvent.setup();
  const region = await card("Sesiones abiertas");
  await user.click(within(region).getByRole("button", { name: "Cerrar sesión en todos los dispositivos" }));
  const dialog = await screen.findByRole("dialog", { name: "Cerrar todas las sesiones" });
  expect(sent(w, "POST", "/auth/logout-all")).toHaveLength(0);
  await user.click(within(dialog).getByRole("button", { name: "Cerrar todas" }));
  expect(await screen.findByRole("heading", { name: "Inicia sesión en tu panel" })).toBeInTheDocument();
  expect(sent(w, "POST", "/auth/logout-all")).toHaveLength(1);
});

// --- Piezas puras ---

test("la lista de países y zonas siempre incluye lo que ya tiene el negocio", () => {
  expect(countryOptions("VE")[0].code).toBe("VE");
  expect(countryOptions("ZZ")[0]).toEqual({ code: "ZZ", name: "ZZ" });
  const zones = zoneOptions("America/Caracas");
  expect(zones.common[0]).toBe("America/Caracas");
  expect(zones.rest).not.toContain("America/Caracas");
  expect(zoneOptions("Mars/Olympus").common[0]).toBe("Mars/Olympus");
});
