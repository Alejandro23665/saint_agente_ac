import { useCallback, useEffect, useRef, useState } from "react";

import { inboxApi } from "../api/inbox";
import type { Conversation, ConversationFilters } from "../api/types";
import { useEvents } from "../layout/dashboard";
import { INBOX_PAGE_SIZE, mergeFirstPage } from "./inbox";
import { errorText } from "./useResource";

export interface InboxList {
  rows: Conversation[];
  loading: boolean; // la primera carga con estos filtros
  loadingMore: boolean;
  error: string | null;
  hasMore: boolean;
  loadMore: () => Promise<void>;
  reload: () => Promise<void>;
}

/**
 * La bandeja con sus filtros: primera página, "ver más antiguas" y recarga sola cuando
 * llega un aviso de /events (sin perder las páginas que ya se abrieron).
 */
export function useInboxList(filters: ConversationFilters): InboxList {
  const [rows, setRows] = useState<Conversation[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const ticket = useRef(0);
  const rowsRef = useRef(rows);
  rowsRef.current = rows;
  const filtersRef = useRef(filters);
  filtersRef.current = filters;
  const key = JSON.stringify(filters);

  const reload = useCallback(async () => {
    const mine = ticket.current;
    try {
      const fresh = await inboxApi.list({ ...filtersRef.current, limit: INBOX_PAGE_SIZE });
      if (mine !== ticket.current) return;
      setRows((previous) => mergeFirstPage(previous, fresh));
      setHasMore((more) => (fresh.length < INBOX_PAGE_SIZE ? false : more));
      setError(null);
    } catch (e) {
      if (mine !== ticket.current) return;
      setError(errorText(e));
    } finally {
      if (mine === ticket.current) setLoading(false);
    }
  }, []);

  // Otros filtros: se empieza de cero.
  useEffect(() => {
    ticket.current += 1;
    const mine = ticket.current;
    setRows([]);
    setLoading(true);
    setLoadingMore(false);
    setError(null);
    inboxApi
      .list({ ...filtersRef.current, limit: INBOX_PAGE_SIZE })
      .then((first) => {
        if (mine !== ticket.current) return;
        setRows(first);
        setHasMore(first.length >= INBOX_PAGE_SIZE);
      })
      .catch((e: unknown) => {
        if (mine === ticket.current) setError(errorText(e));
      })
      .finally(() => {
        if (mine === ticket.current) setLoading(false);
      });
  }, [key]);

  const loadMore = useCallback(async () => {
    const last = rowsRef.current[rowsRef.current.length - 1];
    if (!last?.last_message_at) {
      setHasMore(false);
      return;
    }
    const mine = ticket.current;
    setLoadingMore(true);
    try {
      const older = await inboxApi.list({ ...filtersRef.current, before: last.last_message_at, limit: INBOX_PAGE_SIZE });
      if (mine !== ticket.current) return;
      setRows((previous) => {
        const seen = new Set(previous.map((c) => c.id));
        return [...previous, ...older.filter((c) => !seen.has(c.id))];
      });
      setHasMore(older.length >= INBOX_PAGE_SIZE);
      setError(null);
    } catch (e) {
      if (mine === ticket.current) setError(errorText(e));
    } finally {
      if (mine === ticket.current) setLoadingMore(false);
    }
  }, []);

  // "ready": al (re)conectar, por si algo cambió mientras no había conexión.
  useEvents(["conversation.changed", "inbox.changed", "ready"], () => void reload());

  return { rows, loading, loadingMore, error, hasMore, loadMore, reload };
}
