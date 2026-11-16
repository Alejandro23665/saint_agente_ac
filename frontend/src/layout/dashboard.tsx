/**
 * Los datos del negocio que se ven en la estructura del panel (Inicio y el número de
 * la Bandeja en el menú). Se cargan al entrar y se vuelven a pedir solos cuando llega
 * un aviso por /events.
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

type Part = Exclude<keyof DashboardData, "failed">;

const SOURCES: Record<Part, string> = {
  // La primera página de la bandeja (hasta 100): alcanza para los contadores de Inicio.
  conversations: "/conversations?limit=100",
  numbers: "/numbers",
  hours: "/hours",
  agent: "/agent",
  knowledge: "/knowledge",
  members: "/tenant/members",
};

// Qué partes cambian con cada aviso.
const EVENT_PARTS: Record<string, Part[]> = {
  ready: ["conversations", "numbers", "knowledge"],
  "conversation.changed": ["conversations"],
  "inbox.changed": ["conversations"],
  "numbers.changed": ["numbers"],
  "knowledge.changed": ["knowledge"],
};

const DashboardContext = createContext<DashboardData>(EMPTY);

export function DashboardProvider({ tenantId, children }: { tenantId: string; children: ReactNode }) {
  const [data, setData] = useState<DashboardData>(EMPTY);
  const pending = useRef(new Set<Part>());
  const timer = useRef<number | undefined>(undefined);

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

  useEffect(() => {
    setData(EMPTY);
    void load(Object.keys(SOURCES) as Part[]);
    const flush = () => {
      const parts = [...pending.current];
      pending.current.clear();
      if (parts.length) void load(parts);
    };
    const stop = connectEvents((event: PanelEvent) => {
      for (const part of EVENT_PARTS[event.type] ?? []) pending.current.add(part);
      // Varios avisos seguidos (el cliente manda 3 mensajes) se juntan en una recarga.
      window.clearTimeout(timer.current);
      timer.current = window.setTimeout(flush, 400);
    });
    return () => {
      stop();
      window.clearTimeout(timer.current);
    };
  }, [tenantId, load]);

  return <DashboardContext.Provider value={data}>{children}</DashboardContext.Provider>;
}

export function useDashboard(): DashboardData {
  return useContext(DashboardContext);
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
