import { useCallback, useEffect, useRef, useState } from "react";

import { inboxApi } from "../api/inbox";
import type { Message } from "../api/types";
import { MESSAGES_PAGE_SIZE, mergeLatest } from "./messages";
import { errorText } from "./useResource";

export interface MessageList {
  messages: Message[];
  loading: boolean;
  loadingOlder: boolean;
  error: string | null;
  hasOlder: boolean;
  loadOlder: () => Promise<void>;
  reload: () => Promise<void>;
}

/**
 * Los mensajes de una conversación: la página más reciente, "ver anteriores" hacia arriba
 * y una recarga que mantiene lo que ya se abrió (para ver cómo cambia el estado de lo enviado).
 */
export function useMessages(conversationId: string): MessageList {
  const [messages, setMessages] = useState<Message[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hasOlder, setHasOlder] = useState(false);
  const ticket = useRef(0);
  const ref = useRef(messages);
  ref.current = messages;

  useEffect(() => {
    ticket.current += 1;
    const mine = ticket.current;
    setMessages([]);
    setLoading(true);
    setError(null);
    setHasOlder(false);
    inboxApi
      .messages(conversationId)
      .then((page) => {
        if (mine !== ticket.current) return;
        setMessages(page);
        setHasOlder(page.length >= MESSAGES_PAGE_SIZE);
      })
      .catch((e: unknown) => {
        if (mine === ticket.current) setError(errorText(e));
      })
      .finally(() => {
        if (mine === ticket.current) setLoading(false);
      });
  }, [conversationId]);

  const reload = useCallback(async () => {
    const mine = ticket.current;
    try {
      const page = await inboxApi.messages(conversationId);
      if (mine !== ticket.current) return;
      setMessages((previous) => mergeLatest(previous, page));
      setHasOlder((more) => (page.length < MESSAGES_PAGE_SIZE ? false : more));
      setError(null);
    } catch (e) {
      if (mine === ticket.current) setError(errorText(e));
    }
  }, [conversationId]);

  const loadOlder = useCallback(async () => {
    const first = ref.current[0];
    if (!first) return;
    const mine = ticket.current;
    setLoadingOlder(true);
    try {
      const older = await inboxApi.messages(conversationId, first.created_at);
      if (mine !== ticket.current) return;
      setMessages((previous) => {
        const seen = new Set(previous.map((m) => m.id));
        return [...older.filter((m) => !seen.has(m.id)), ...previous];
      });
      setHasOlder(older.length >= MESSAGES_PAGE_SIZE);
    } catch (e) {
      if (mine === ticket.current) setError(errorText(e));
    } finally {
      if (mine === ticket.current) setLoadingOlder(false);
    }
  }, [conversationId]);

  return { messages, loading, loadingOlder, error, hasOlder, loadOlder, reload };
}
