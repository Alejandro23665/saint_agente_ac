/**
 * Avisos en tiempo real (GET /events, bloque 5.6).
 *
 * Se usa fetch con streaming y no EventSource porque EventSource no deja mandar la
 * cabecera Authorization. Si la conexión se corta, se vuelve a abrir sola esperando
 * cada vez un poco más (2 s, 4 s, 8 s... hasta 30 s). Si llega "session.expired", se
 * renueva el pase y se reconecta.
 */
import { getAccessToken, refreshSession } from "../api/client";

export interface PanelEvent {
  type: string;
  conversation_id?: string;
  source_id?: string;
}

/** Parte el texto recibido en avisos SSE. Devuelve los avisos y lo que sobró. */
export function parseSse(buffer: string): { events: PanelEvent[]; rest: string } {
  const events: PanelEvent[] = [];
  const blocks = buffer.replace(/\r\n/g, "\n").split("\n\n");
  const rest = blocks.pop() ?? "";
  for (const block of blocks) {
    const data = block
      .split("\n")
      .filter((line) => line.startsWith("data:"))
      .map((line) => line.slice(5).trimStart())
      .join("\n");
    if (!data) continue; // ": ping" y líneas sin datos
    try {
      events.push(JSON.parse(data) as PanelEvent);
    } catch {
      // Un aviso mal formado se ignora.
    }
  }
  return { events, rest };
}

const MAX_WAIT_MS = 30_000;

/** Abre la conexión y llama a onEvent por cada aviso. Devuelve cómo cerrarla. */
export function connectEvents(onEvent: (event: PanelEvent) => void): () => void {
  let stopped = false;
  let controller: AbortController | null = null;
  let wait = 2_000;

  const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

  async function once(): Promise<"retry" | "renew"> {
    controller = new AbortController();
    const token = getAccessToken();
    if (!token) return "renew";
    const response = await fetch("/events", {
      headers: { Authorization: `Bearer ${token}` },
      signal: controller.signal,
    });
    if (response.status === 401) return "renew";
    if (!response.ok || !response.body) return "retry";
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) return "retry";
      buffer += decoder.decode(value, { stream: true });
      const parsed = parseSse(buffer);
      buffer = parsed.rest;
      for (const event of parsed.events) {
        if (event.type === "session.expired") return "renew";
        // "ready" también se pasa: al (re)conectar, el panel recarga lo que pudo cambiar
        // mientras no había conexión.
        if (event.type === "ready") wait = 2_000;
        onEvent(event);
      }
    }
  }

  (async () => {
    while (!stopped) {
      let outcome: "retry" | "renew" = "retry";
      try {
        outcome = await once();
      } catch {
        outcome = "retry";
      }
      if (stopped) break;
      if (outcome === "renew") {
        // Si no se puede renovar, el cliente de la API ya devolvió al panel al inicio.
        const renewed = await refreshSession().catch(() => null);
        if (!renewed) break;
        continue;
      }
      await sleep(wait);
      wait = Math.min(wait * 2, MAX_WAIT_MS);
    }
  })();

  return () => {
    stopped = true;
    controller?.abort();
  };
}
