/**
 * Los datos del negocio que se ven en la estructura del panel (Inicio y el número de
 * la Bandeja en el menú). Se cargan al entrar y se vuelven a pedir solos cuando llega
 * un aviso por /events.
 *
 * Aquí vive también la única conexión a /events del panel: las pantallas escuchan los
 * avisos con useEvents() en vez de abrir otra. Y si una pantalla cambia algo que Inicio
 * muestra (el horario, el agente, el equipo), llama a refresh() para que se actualice.
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";

import { api } from "../api/client";
import type { Agent, Conversation, Hours, KnowledgeSource, Member, WhatsappNumber } from "../api/types";
import { connectEvents } from "../realtime/events";
import type { PanelEvent } from "../realtime/events";

export interface DashboardData {
  conversations: Conversation[] | null;
  numbers: WhatsappNumber[] | null;
  hours: Hours | null;
  agent: Agent | null;
  knowledge: KnowledgeSource[] | null;
  members: Member[] | null;
  failed: boolean; // alguna parte no se pudo cargar
}

const EMPTY: DashboardData = {
  conversations: null,
  numbers: null,
  hours: null,
  agent: null,
  knowledge: null,
  members: null,
  failed: false,
};

export type Part = Exclude<keyof DashboardData, "failed">;

const SOURCES: Record<Part, string> = {
  // La primera página de la bandeja (hasta 100): alcanza para los contadores de Inicio.
  conversations: "/conversations?limit=100",
  numbers: "/numbers",
  hours: "/hours",
  agent: "/agent",
  knowledge: "/knowledge",
  members: "/tenant/members",
};

const ALL = Object.keys(SOURCES) as Part[];

// Qué partes cambian con cada aviso. Al (re)conectar ("ready") se recarga todo: mientras
// no hubo conexión pudo cambiar cualquier cosa. El horario, el agente y el equipo no
// tienen aviso propio: los recarga la pantalla que los cambia (refresh).
const EVENT_PARTS: Record<string, Part[]> = {
  ready: ALL,
  "conversation.changed": ["conversations"],
  "inbox.changed": ["conversations"],
  "numbers.changed": ["numbers"],
  "knowledge.changed": ["knowledge"],
};

type Listener = (event: PanelEvent) => void;

interface DashboardContextValue {
  data: DashboardData;
  refresh: (parts: Part[]) => void;
  subscribe: (listener: Listener) => () => void;
}

const DashboardContext = createContext<DashboardContextValue>({
  data: EMPTY,
  refresh: () => {},
  subscribe: () => () => {},
});

export function DashboardProvider({ tenantId, children }: { tenantId: string; children: ReactNode }) {
  const [data, setData] = useState<DashboardData>(EMPTY);
  const pending = useRef(new Set<Part>());
  const timer = useRef<number | undefined>(undefined);
  const listeners = useRef(new Set<Listener>());

  const load = useCallback(async (parts: Part[]) => {
    const results = await Promise.all(
      parts.map((part) =>
        api<unknown>(SOURCES[part]).then(
          (value) => [part, value, false] as const,
          () => [part, null, true] as const,
        ),
      ),
    );
    setData((current) => {
      const next = { ...current, failed: false };
      for (const [part, value, failed] of results) {
        if (failed) next.failed = true;
        else (next as Record<Part, unknown>)[part] = value;
      }
      return next;
    });
  }, []);

  const flushSoon = useCallback(() => {
    // Varios avisos seguidos (el cliente manda 3 mensajes) se juntan en una recarga.
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      const parts = [...pending.current];
      pending.current.clear();
      if (parts.length) void load(parts);
    }, 400);
  }, [load]);

  const refresh = useCallback(
    (parts: Part[]) => {
      for (const part of parts) pending.current.add(part);
      flushSoon();
    },
    [flushSoon],
  );

  const subscribe = useCallback((listener: Listener) => {
    listeners.current.add(listener);
    return () => {
      listeners.current.delete(listener);
    };
  }, []);

  useEffect(() => {
    setData(EMPTY);
    pending.current.clear();
    void load(ALL);
    let firstReady = true;
    const stop = connectEvents((event: PanelEvent) => {
      // El primer "ready" llega justo después de la carga inicial: no hace falta repetirla.
      const skip = event.type === "ready" && firstReady;
      if (event.type === "ready") firstReady = false;
      if (!skip) for (const part of EVENT_PARTS[event.type] ?? []) pending.current.add(part);
      flushSoon();
      for (const listener of [...listeners.current]) {
        try {
          listener(event);
        } catch {
          // Una pantalla con un error no corta los avisos de las demás.
        }
      }
    });
    return () => {
      stop();
      window.clearTimeout(timer.current);
    };
  }, [tenantId, load, flushSoon]);

  const value = useMemo(() => ({ data, refresh, subscribe }), [data, refresh, subscribe]);
  return <DashboardContext.Provider value={value}>{children}</DashboardContext.Provider>;
}

export function useDashboard(): DashboardData {
  return useContext(DashboardContext).data;
}

/** Pedir que Inicio y el menú vuelvan a cargar algo que esta pantalla acaba de cambiar. */
export function useDashboardRefresh(): (parts: Part[]) => void {
  return useContext(DashboardContext).refresh;
}

/**
 * Escuchar los avisos de /events desde una pantalla. `types` filtra cuáles; "ready"
 * llega al (re)conectar, para recargar lo que pudo cambiar sin conexión.
 */
export function useEvents(types: string[], onEvent: Listener): void {
  const { subscribe } = useContext(DashboardContext);
  const handler = useRef(onEvent);
  handler.current = onEvent;
  const key = types.join(",");
  useEffect(() => {
    const wanted = new Set(key.split(","));
    return subscribe((event) => {
      if (wanted.has(event.type)) handler.current(event);
    });
  }, [subscribe, key]);
}

export interface InboxCounts {
  open: number;
  waiting: number;
  human: number;
  mine: number;
  capped: boolean; // hay más de 100: los números son "100+"
}

export function inboxCounts(conversations: Conversation[], myId: string): InboxCounts {
  return {
    open: conversations.length,
    waiting: conversations.filter((c) => c.waiting_reply).length,
    human: conversations.filter((c) => c.mode === "human").length,
    mine: conversations.filter((c) => c.assigned?.user_id === myId).length,
    capped: conversations.length >= 100,
  };
}

export function useInboxCounts(myId: string): InboxCounts | null {
  const { conversations } = useDashboard();
  return useMemo(() => (conversations ? inboxCounts(conversations, myId) : null), [conversations, myId]);
}
