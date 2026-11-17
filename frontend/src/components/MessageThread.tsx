import { useLayoutEffect, useRef, useState } from "react";

import type { Message } from "../api/types";
import { clock } from "../lib/format";
import { groupByDay, messageBody, senderLabel } from "../lib/messages";
import { MESSAGE_STATUS, statusOf } from "../lib/status";
import type { MessageList } from "../lib/useMessages";
import { Alert } from "./Alert";
import { Button } from "./Button";
import { Spinner } from "./Feedback";

function Bubble({ message }: { message: Message }) {
  const outbound = message.direction === "outbound";
  const label = senderLabel(message);
  const body = messageBody(message);
  const status = outbound ? statusOf(MESSAGE_STATUS, message.status) : null;
  const kind = outbound ? `bubble-out bubble-${message.sender_type}` : "bubble-in";

  return (
    <div className={outbound ? "msg msg-out" : "msg msg-in"}>
      {label && <span className="msg-label">{label}</span>}
      <div className={`bubble ${kind}`}>
        {body.media && <span className="msg-media">{body.media}</span>}
        {body.text && <p>{body.text}</p>}
        {body.note && <em className="msg-note">{body.note}</em>}
        {!body.media && !body.text && <em className="msg-note">Mensaje vacío</em>}
      </div>
      <span className="msg-meta">
        <time dateTime={message.created_at}>{clock(message.created_at)}</time>
        {status && (
          <span className={message.status === "failed" ? "msg-status msg-status-failed" : "msg-status"}>
            {" · "}
            {status.label}
          </span>
        )}
      </span>
      {message.status === "failed" && message.error && <span className="msg-error">{message.error}</span>}
    </div>
  );
}

/**
 * Los mensajes, del más viejo al más nuevo. Si estabas leyendo lo de arriba, un mensaje
 * nuevo no te mueve: aparece un botón para bajar. Al cargar los anteriores, la pantalla
 * se queda en el mismo lugar.
 */
export function MessageThread({ list }: { list: MessageList }) {
  const box = useRef<HTMLDivElement>(null);
  const stick = useRef(true); // ¿está leyendo lo último?
  const heightBefore = useRef<number | null>(null); // para no saltar al cargar anteriores
  const lastId = useRef<string | undefined>(undefined);
  const [newBelow, setNewBelow] = useState(false);
  const { messages } = list;

  useLayoutEffect(() => {
    const el = box.current;
    if (!el) return;
    const last = messages[messages.length - 1];
    if (heightBefore.current !== null) {
      el.scrollTop += el.scrollHeight - heightBefore.current;
      heightBefore.current = null;
      lastId.current = last?.id;
      return;
    }
    if (last && last.id !== lastId.current) {
      const first = lastId.current === undefined;
      lastId.current = last.id;
      if (first || stick.current || last.direction === "outbound") {
        el.scrollTop = el.scrollHeight;
        setNewBelow(false);
      } else {
        setNewBelow(true);
      }
    }
  }, [messages]);

  function onScroll() {
    const el = box.current;
    if (!el) return;
    stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    if (stick.current) setNewBelow(false);
  }

  function older() {
    heightBefore.current = box.current?.scrollHeight ?? null;
    void list.loadOlder();
  }

  function toBottom() {
    const el = box.current;
    if (el) el.scrollTop = el.scrollHeight;
    stick.current = true;
    setNewBelow(false);
  }

  const groups = groupByDay(messages);

  return (
    <div className="thread-wrap">
      <div ref={box} className="thread" role="log" aria-label="Mensajes de la conversación" onScroll={onScroll}>
        {list.error && (
          <Alert kind="error" title="No pudimos cargar los mensajes">
            {list.error}
          </Alert>
        )}
        {list.loading ? (
          <div className="center-box">
            <Spinner label="Cargando los mensajes" size={28} />
          </div>
        ) : (
          <>
            {list.hasOlder && (
              <div className="thread-older">
                <Button variant="secondary" small onClick={older} busy={list.loadingOlder}>
                  Ver mensajes anteriores
                </Button>
              </div>
            )}
            {messages.length === 0 && !list.error && <p className="muted thread-empty">Todavía no hay mensajes en esta conversación.</p>}
            {groups.map((group) => (
              <section key={group.key} aria-label={group.label}>
                <h3 className="day">{group.label}</h3>
                {group.messages.map((m) => (
                  <Bubble key={m.id} message={m} />
                ))}
              </section>
            ))}
          </>
        )}
      </div>
      {newBelow && (
        <button type="button" className="thread-new" onClick={toBottom}>
          Mensajes nuevos ↓
        </button>
      )}
    </div>
  );
}
