import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

import { App } from "../App";
import { storeSession } from "../api/client";
import type { UsageDay, UsagePeriod, UsageReport } from "../api/types";
import { SessionProvider } from "../auth/session";
import { ToastProvider } from "../components/Toast";
import { barHeight, compact, dayLabel, isEmpty, METRICS, money, parseDay, periodLine, seriesMax, trackingNotice } from "../lib/usage";

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function day(date: string, overrides: Partial<UsageDay> = {}): UsageDay {
  return { day: date, conversations: 0, inbound_messages: 0, outbound_messages: 0, ai_replies: 0, input_tokens: 0, output_tokens: 0, ...overrides };
}

function report(period: UsagePeriod = "this_month", overrides: Partial<UsageReport> = {}): UsageReport {
  const days = [
    day("2026-11-14", { conversations: 2, inbound_messages: 10, outbound_messages: 6, ai_replies: 5 }),
    day("2026-11-15"),
    day("2026-11-16", { conversations: 1, inbound_messages: 32, outbound_messages: 20, ai_replies: 18, input_tokens: 40_000, output_tokens: 5_000 }),
  ];
  const totals = {
    conversations: 3,
    inbound_messages: 42,
    outbound_messages: 26,
    ai_replies: 23,
    input_tokens: 40_000,
    output_tokens: 5_000,
  };
  return {
    period,
    start: "2026-11-14",
    end: "2026-11-16",
    timezone: "America/Caracas",
    tracking_since: "2026-11-14",
    totals,
    pricing: { configured: false, input_usd_per_million: null, output_usd_per_million: null },
    estimated_cost_usd: null,
    days,
    ...overrides,
  };
}

interface World {
  role: string;
  build: (period: UsagePeriod) => UsageReport;
  fail?: boolean;
  calls: string[];
}

function fakeApi(w: World) {
  const tenant = { id: "t1", name: "Panadería Prueba", slug: "panaderia", role: w.role };
  const session = {
    access_token: "pase",
    expires_at: new Date(Date.now() + 900_000).toISOString(),
    user: { id: "u1", email: "andre@saint.ai", full_name: "Andre Pérez" },
    tenants: [tenant],
    active_tenant: tenant,
  };
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = String(input);
    w.calls.push(url);
    if (url === "/auth/refresh") return json(200, session);
    if (url === "/events") return new Response(null, { status: 503 });
    if (url.startsWith("/usage")) {
      if (w.fail) return json(500, { detail: "El servidor falló" });
      return json(200, w.build(new URL(url, "http://x").searchParams.get("period") as UsagePeriod));
    }
    if (url === "/hours") return json(200, { days: {}, after_hours_message: null, open_now: true, timezone: "America/Caracas", can_edit: true });
    if (url === "/agent") return json(200, { version: 1 });
    return json(200, []);
  });
}

function renderUsage() {
  return render(
    <MemoryRouter initialEntries={["/consumo"]}>
      <SessionProvider>
        <ToastProvider>
          <App />
        </ToastProvider>
      </SessionProvider>
    </MemoryRouter>,
  );
}

const cards = () => screen.findByRole("region", { name: "Resumen del periodo" });

afterEach(() => {
  vi.restoreAllMocks();
  storeSession(null);
  localStorage.clear();
});

test("muestra las cifras del periodo y el gráfico", async () => {
  fakeApi({ role: "owner", build: (p) => report(p), calls: [] });
  renderUsage();
  const summary = await cards();
  expect(within(summary).getByText("Mensajes recibidos").nextSibling).toHaveTextContent("42");
  expect(within(summary).getByText("Mensajes enviados").nextSibling).toHaveTextContent("26");
  expect(within(summary).getByText("Respuestas de IA").nextSibling).toHaveTextContent("23");
  expect(within(summary).getByText("Conversaciones nuevas").nextSibling).toHaveTextContent("3");
  expect(within(summary).getByText("Tokens de IA").nextSibling).toHaveTextContent("45 mil");
  expect(within(summary).getByText(/40\.000 de entrada · 5\.000 de salida/)).toBeInTheDocument();
  expect(screen.getByText(/Del 14 al 16 de noviembre · zona horaria America\/Caracas/)).toBeInTheDocument();

  const chart = screen.getByRole("img", { name: /Mensajes por día, del 14 nov al 16 nov/ });
  expect(chart).toHaveAccessibleName(expect.stringContaining("El día más alto: 32"));
});

test("sin precios en el .env no inventa un costo; con precios lo muestra", async () => {
  const w: World = { role: "owner", build: (p) => report(p), calls: [] };
  fakeApi(w);
  const first = renderUsage();
  const summary = await cards();
  expect(within(summary).getByText("Sin precios")).toBeInTheDocument();
  expect(within(summary).getByText(/Pon los precios en el \.env/)).toBeInTheDocument();
  first.unmount();

  vi.restoreAllMocks();
  fakeApi({
    role: "owner",
    calls: [],
    build: (p) =>
      report(p, { pricing: { configured: true, input_usd_per_million: 1, output_usd_per_million: 5 }, estimated_cost_usd: 3.5 }),
  });
  renderUsage();
  expect(await within(await cards()).findByText("US$ 3,50")).toBeInTheDocument();
});

test("cambiar de periodo vuelve a pedir los datos de ese periodo", async () => {
  const w: World = {
    role: "owner",
    calls: [],
    build: (p) => (p === "last_7_days" ? report(p, { totals: { ...report().totals, inbound_messages: 7 } }) : report(p)),
  };
  fakeApi(w);
  renderUsage();
  const user = userEvent.setup();
  await cards();
  expect(w.calls.filter((c) => c.startsWith("/usage"))).toEqual(["/usage?period=this_month"]);

  await user.click(screen.getByRole("button", { name: "Últimos 7 días" }));
  await waitFor(() => expect(w.calls).toContain("/usage?period=last_7_days"));
  await waitFor(() => expect(within(screen.getByRole("region", { name: "Resumen del periodo" })).getByText("Mensajes recibidos").nextSibling).toHaveTextContent("7"));
  expect(screen.getByRole("button", { name: "Últimos 7 días" })).toHaveAttribute("aria-pressed", "true");
});

test("el gráfico cambia según lo que se quiera ver", async () => {
  fakeApi({ role: "owner", build: (p) => report(p), calls: [] });
  renderUsage();
  const user = userEvent.setup();
  await cards();
  await user.click(screen.getByRole("button", { name: "Tokens de IA" }));
  expect(screen.getByRole("img", { name: /Tokens de IA por día.*El día más alto: 40\.000/ })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Tokens de IA por día" })).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Conversaciones nuevas" }));
  expect(screen.getByRole("img", { name: /Conversaciones nuevas por día.*El día más alto: 2/ })).toBeInTheDocument();
});

test("los números exactos de cada día están en una tabla", async () => {
  fakeApi({ role: "owner", build: (p) => report(p), calls: [] });
  renderUsage();
  await cards();
  const table = screen.getByRole("table", { hidden: true });
  const row = within(table).getByRole("row", { name: /16 nov/, hidden: true });
  expect(within(row).getAllByRole("cell", { hidden: true }).map((c) => c.textContent)).toEqual(["1", "32", "20", "18", "40.000", "5.000"]);
  expect(within(table).getAllByRole("row", { hidden: true })).toHaveLength(4); // encabezado + 3 días
});

test("avisa si solo hay datos desde una fecha posterior al inicio del periodo", async () => {
  fakeApi({ role: "owner", build: (p) => report(p, { tracking_since: "2026-11-16" }), calls: [] });
  renderUsage();
  expect(await screen.findByText(/Solo hay datos desde el 16 nov/)).toBeInTheDocument();
});

test("sin ningún dato explica cuándo empieza a contarse y no dibuja el gráfico", async () => {
  const empty = { conversations: 0, inbound_messages: 0, outbound_messages: 0, ai_replies: 0, input_tokens: 0, output_tokens: 0 };
  fakeApi({
    role: "owner",
    calls: [],
    build: (p) => report(p, { tracking_since: null, totals: empty, days: report().days.map((d) => day(d.day)) }),
  });
  renderUsage();
  expect(await screen.findByText(/Todavía no hay consumo registrado/)).toBeInTheDocument();
  expect(screen.queryByRole("img", { name: /por día/ })).not.toBeInTheDocument();
  expect(screen.queryByRole("table", { hidden: true })).not.toBeInTheDocument();
});

test("un agente no ve el consumo y ni siquiera se le pide a la API", async () => {
  const w: World = { role: "agent", build: (p) => report(p), calls: [] };
  fakeApi(w);
  renderUsage();
  expect(await screen.findByText("El consumo lo ve el dueño o un administrador")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Actualizar" })).not.toBeInTheDocument();
  expect(w.calls.some((c) => c.startsWith("/usage"))).toBe(false);
});

test("si la API falla, se ve el error y se puede reintentar", async () => {
  const w: World = { role: "owner", build: (p) => report(p), fail: true, calls: [] };
  fakeApi(w);
  renderUsage();
  const user = userEvent.setup();
  expect(await screen.findByText("No pudimos cargar el consumo")).toBeInTheDocument();
  expect(screen.getByText(/El servidor falló/)).toBeInTheDocument();
  w.fail = false;
  await user.click(screen.getByRole("button", { name: "Reintentar" }));
  expect(await cards()).toBeInTheDocument();
});

// --- Piezas puras ---

test("formatos de números, dinero y fechas", () => {
  expect(compact(950)).toBe("950");
  expect(compact(12_345)).toBe("12,3 mil");
  expect(compact(45_000)).toBe("45 mil");
  expect(compact(1_500_000)).toBe("1,5 M");
  expect(compact(2_000_000)).toBe("2 M");
  expect(money(3.5)).toBe("US$ 3,50");
  expect(money(0.0123)).toBe("US$ 0,0123");
  expect(parseDay("2026-11-16").getDate()).toBe(16);
  expect(dayLabel("2026-11-16")).toBe("16 nov");
  expect(periodLine("2026-11-01", "2026-11-16")).toBe("Del 1 al 16 de noviembre");
  expect(periodLine("2026-11-16", "2026-11-16")).toBe("El 16 de noviembre");
  expect(periodLine("2025-12-17", "2026-01-15")).toContain("17 dic");
});

test("las barras: proporcionales, y un valor pequeño no desaparece", () => {
  const days = [day("2026-11-14", { inbound_messages: 100 }), day("2026-11-15", { inbound_messages: 1 }), day("2026-11-16")];
  const series = METRICS[0].series;
  expect(seriesMax(days, series)).toBe(100);
  expect(barHeight(100, 100)).toBe(100);
  expect(barHeight(50, 100)).toBe(50);
  expect(barHeight(1, 100)).toBe(3); // se ve aunque sea poco
  expect(barHeight(0, 100)).toBe(0);
  expect(barHeight(5, 0)).toBe(0);
});

test("cuándo hay datos parciales o vacíos", () => {
  expect(trackingNotice({ start: "2026-11-01", tracking_since: "2026-11-01" })).toBeNull();
  expect(trackingNotice({ start: "2026-11-01", tracking_since: "2026-10-01" })).toBeNull();
  expect(trackingNotice({ start: "2026-11-01", tracking_since: "2026-11-10" })).toContain("desde el 10 nov");
  expect(trackingNotice({ start: "2026-11-01", tracking_since: null })).toContain("Todavía no hay consumo");
  expect(isEmpty(report())).toBe(false);
  expect(isEmpty({ totals: { conversations: 0, inbound_messages: 0, outbound_messages: 0, ai_replies: 0, input_tokens: 0, output_tokens: 0 } })).toBe(true);
});
