import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router-dom";

import { App } from "../App";
import { storeSession } from "../api/client";
import { SessionProvider } from "../auth/session";

const TOKEN = "t".repeat(43);
const USER = { id: "u2", email: "ana@panaderia.com", full_name: "Ana Rivas" };
const PANADERIA = { id: "t1", name: "Panadería Prueba", slug: "panaderia", role: "agent" };

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function preview(hasAccount: boolean) {
  return {
    tenant_name: "Panadería Prueba",
    email: "ana@panaderia.com",
    role: "agent",
    expires_at: new Date(Date.now() + 72 * 3600_000).toISOString(),
    has_account: hasAccount,
  };
}

const SESSION = {
  access_token: "pase",
  expires_at: new Date(Date.now() + 900_000).toISOString(),
  user: USER,
  tenants: [PANADERIA],
  active_tenant: PANADERIA,
};

// API simulada: sin sesión; responde la invitación como diga cada prueba.
function fakeApi(handlers: { preview: () => Response; accept?: (body: Record<string, unknown>) => Response }) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    if (url === "/auth/refresh") return json(401, { detail: "Tu sesión terminó." });
    if (url === "/auth/invitations/preview") {
      expect(JSON.parse(String(init?.body))).toEqual({ token: TOKEN });
      return handlers.preview();
    }
    if (url === "/auth/invitations/accept") return handlers.accept!(JSON.parse(String(init?.body)));
    if (url === "/events") return new Response(null, { status: 503 });
    if (url === "/hours") return json(200, { days: {}, open_now: true, timezone: "America/Caracas" });
    if (url === "/agent") return json(200, { version: 1 });
    return json(200, []);
  });
}

function Where() {
  const location = useLocation();
  return <span data-testid="donde">{location.pathname + location.hash}</span>;
}

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <SessionProvider>
        <App />
        <Where />
      </SessionProvider>
    </MemoryRouter>,
  );
}

afterEach(() => {
  vi.restoreAllMocks();
  storeSession(null);
  localStorage.clear();
});

test("sin cuenta: crea la cuenta y entra al negocio", async () => {
  fakeApi({
    preview: () => json(200, preview(false)),
    accept: (body) => {
      expect(body).toEqual({ token: TOKEN, password: "una-clave-larga", full_name: "Ana Rivas", remember: true });
      return json(200, SESSION);
    },
  });
  renderAt(`/invitacion#${TOKEN}`);
  const user = userEvent.setup();

  expect(await screen.findByRole("heading", { name: "Te invitaron a Panadería Prueba" })).toBeInTheDocument();
  // El token se quita de la dirección apenas se lee.
  await waitFor(() => expect(screen.getByTestId("donde")).toHaveTextContent(/^\/invitacion$/));

  await user.type(screen.getByLabelText("Tu nombre"), "Ana Rivas");
  await user.type(screen.getByLabelText("Contraseña nueva"), "corta");
  await user.click(screen.getByRole("button", { name: "Crear mi cuenta y entrar" }));
  expect(screen.getByText("Usa al menos 10 caracteres.")).toBeInTheDocument();

  await user.clear(screen.getByLabelText("Contraseña nueva"));
  await user.type(screen.getByLabelText("Contraseña nueva"), "una-clave-larga");
  await user.type(screen.getByLabelText("Repite la contraseña"), "otra-clave-larga");
  await user.click(screen.getByRole("button", { name: "Crear mi cuenta y entrar" }));
  expect(screen.getByText("Las dos contraseñas no coinciden.")).toBeInTheDocument();

  await user.clear(screen.getByLabelText("Repite la contraseña"));
  await user.type(screen.getByLabelText("Repite la contraseña"), "una-clave-larga");
  await user.click(screen.getByRole("button", { name: "Crear mi cuenta y entrar" }));
  expect(await screen.findByRole("heading", { name: "Hola, Ana" })).toBeInTheDocument();
});

test("con cuenta: pide su contraseña y avisa si no es correcta", async () => {
  let tries = 0;
  fakeApi({
    preview: () => json(200, preview(true)),
    accept: (body) => {
      tries += 1;
      expect(body.full_name).toBeNull();
      return tries === 1 ? json(401, { detail: "La contraseña no es correcta" }) : json(200, SESSION);
    },
  });
  renderAt(`/invitacion#${TOKEN}`);
  const user = userEvent.setup();

  expect(await screen.findByText(/Ya tienes cuenta en Saint/)).toBeInTheDocument();
  expect(screen.queryByLabelText("Tu nombre")).not.toBeInTheDocument();
  await user.type(screen.getByLabelText("Tu contraseña"), "mala-clave");
  await user.click(screen.getByRole("button", { name: "Aceptar y entrar" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("La contraseña no es correcta");

  await user.clear(screen.getByLabelText("Tu contraseña"));
  await user.type(screen.getByLabelText("Tu contraseña"), "la-buena-clave");
  await user.click(screen.getByRole("button", { name: "Aceptar y entrar" }));
  expect(await screen.findByRole("heading", { name: "Hola, Ana" })).toBeInTheDocument();
});

test("una invitación vencida o usada lo explica", async () => {
  fakeApi({ preview: () => json(404, { detail: "Esta invitación ya no es válida." }) });
  renderAt(`/invitacion#${TOKEN}`);
  expect(await screen.findByRole("heading", { name: "Esta invitación ya no sirve" })).toBeInTheDocument();
});

test("un enlace sin token no llama a la API", async () => {
  const fetch = fakeApi({ preview: () => json(500, {}) });
  renderAt("/invitacion");
  expect(await screen.findByRole("heading", { name: "Esta invitación ya no sirve" })).toBeInTheDocument();
  expect(fetch.mock.calls.some(([url]) => url === "/auth/invitations/preview")).toBe(false);
});
