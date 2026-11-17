import { useEffect, useState } from "react";
import { Link, useLocation, useParams, useSearchParams } from "react-router-dom";

import type { Conversation } from "../api/types";
import { Alert } from "../components/Alert";
import { Button } from "../components/Button";
import { ConversationPane } from "../components/ConversationPane";
import { EmptyState, Spinner, StatusPill } from "../components/Feedback";
import { SelectField } from "../components/Field";
import { Icon } from "../components/Icon";
import { useDashboard } from "../layout/dashboard";
import { phone as formatPhone, timeAgo } from "../lib/format";
import { contactName, filtersFromParams, hasActiveFilters, previewText } from "../lib/inbox";
import { MODE, statusOf } from "../lib/status";
import { useInboxList } from "../lib/useInboxList";
import { initials } from "../auth/session";

function ConversationRow({ conversation: c, selected, search }: { conversation: Conversation; selected: boolean; search: string }) {
  const mode = statusOf(MODE, c.mode);
  return (
    <Link
      to={{ pathname: `/bandeja/${c.id}`, search }}
      className={selected ? "conv-row conv-row-selected" : "conv-row"}
      aria-current={selected ? "true" : undefined}
    >
      <span className="avatar">{initials(contactName(c))}</span>
      <span className="conv-main">
        <span className="conv-top">
          <strong>{contactName(c)}</strong>
          {c.last_message_at && <time dateTime={c.last_message_at}>{timeAgo(c.last_message_at)}</time>}
        </span>
        <span className={c.waiting_reply ? "conv-preview conv-preview-waiting" : "conv-preview"}>{previewText(c)}</span>
        <span className="conv-tags">
          <StatusPill tone={mode.tone}>{mode.label}</StatusPill>
          {c.waiting_reply && <StatusPill tone="warn">Espera respuesta</StatusPill>}
          {c.assigned && <span className="conv-assigned">Asignada a {c.assigned.name ?? "alguien del equipo"}</span>}
        </span>
      </span>
    </Link>
  );
}

export function Inbox() {
  const { id } = useParams();
  const location = useLocation();
  const [params, setParams] = useSearchParams();
  const { numbers } = useDashboard();
  const filters = filtersFromParams(params);
  const list = useInboxList(filters);
  const [search, setSearch] = useState(filters.q ?? "");

  function setParam(name: string, value: string | null) {
    const next = new URLSearchParams(params);
    if (value) next.set(name, value);
    else next.delete(name);
    setParams(next, { replace: true });
  }

  // La búsqueda espera a que termines de escribir.
  useEffect(() => {
    const wanted = search.trim();
    if (wanted === (params.get("q")?.trim() ?? "")) return;
    const timer = window.setTimeout(() => setParam("q", wanted || null), 300);
    return () => window.clearTimeout(timer);
  }, [search]);

  // Si la búsqueda cambia desde fuera (atrás del navegador, "Quitar filtros"), el cuadro se
  // pone al día. Cambiar otro filtro no toca lo que estás escribiendo.
  const queryParam = params.get("q") ?? "";
  useEffect(() => {
    setSearch((current) => (current.trim() === queryParam.trim() ? current : queryParam));
  }, [queryParam]);

  const active = hasActiveFilters(filters);
  const several = (numbers?.length ?? 0) > 1;
  const listLink = { pathname: "/bandeja", search: location.search };

  function clear() {
    setParams(new URLSearchParams(), { replace: true });
    setSearch("");
  }

  return (
    <main className="inbox" data-detail={id ? "true" : "false"}>
      <aside className="inbox-list" aria-label="Conversaciones">
        <div className="inbox-filters">
          <h1>Bandeja</h1>
          <div className="search">
            <Icon name="chat" size={18} />
            <input
              type="search"
              className="input"
              placeholder="Buscar por nombre o teléfono"
              aria-label="Buscar por nombre o teléfono"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
          <div className="segmented" role="group" aria-label="Estado de la conversación">
            <button type="button" aria-pressed={filters.status === "open"} onClick={() => setParam("status", null)}>
              Abiertas
            </button>
            <button type="button" aria-pressed={filters.status === "closed"} onClick={() => setParam("status", "closed")}>
              Cerradas
            </button>
          </div>
          <div className="filter-row">
            <SelectField label="Quién atiende" value={filters.mode ?? ""} onChange={(e) => setParam("mode", e.target.value || null)}>
              <option value="">Todas</option>
              <option value="bot">Agente IA</option>
              <option value="human">Una persona</option>
            </SelectField>
            {several && (
              <SelectField label="Número" value={filters.number_id ?? ""} onChange={(e) => setParam("number", e.target.value || null)}>
                <option value="">Todos</option>
                {numbers?.map((n) => (
                  <option key={n.id} value={n.id}>
                    {n.phone ? formatPhone(n.phone) : "Sin vincular"}
                  </option>
                ))}
              </SelectField>
            )}
          </div>
          <label className="check" htmlFor="solo-mias">
            <input id="solo-mias" type="checkbox" checked={Boolean(filters.mine)} onChange={(e) => setParam("mine", e.target.checked ? "1" : null)} />
            Solo las asignadas a mí
          </label>
        </div>

        {list.error && (
          <Alert kind="error" title="No pudimos cargar la bandeja">
            {list.error}{" "}
            <button type="button" className="link-button" onClick={() => void list.reload()}>
              Reintentar
            </button>
          </Alert>
        )}

        <div className="conv-list" role="list" aria-label="Lista de conversaciones">
          {list.loading ? (
            <div className="center-box">
              <Spinner label="Cargando las conversaciones" size={28} />
            </div>
          ) : list.rows.length === 0 && !list.error ? (
            active ? (
              <EmptyState
                icon="chat"
                title="Ninguna conversación coincide"
                action={
                  <Button variant="secondary" small onClick={clear}>
                    Quitar filtros
                  </Button>
                }
              >
                Prueba con otro nombre o cambia los filtros.
              </EmptyState>
            ) : (
              <EmptyState icon="chat" title="Todavía no hay conversaciones">
                Cuando un cliente escriba a tu WhatsApp, aparecerá aquí al instante.
              </EmptyState>
            )
          ) : (
            list.rows.map((c) => (
              <div key={c.id} role="listitem">
                <ConversationRow conversation={c} selected={c.id === id} search={location.search} />
              </div>
            ))
          )}
          {list.hasMore && !list.loading && (
            <div className="conv-more">
              <Button variant="secondary" small onClick={() => void list.loadMore()} busy={list.loadingMore}>
                Ver más antiguas
              </Button>
            </div>
          )}
        </div>
      </aside>

      <div className="inbox-pane">
        {id ? (
          <ConversationPane key={id} id={id} backTo={listLink.pathname + listLink.search} />
        ) : (
          <EmptyState icon="chat" title="Elige una conversación">
            Toca una de la lista para ver quién es y cómo la atiende tu agente.
          </EmptyState>
        )}
      </div>
    </main>
  );
}
