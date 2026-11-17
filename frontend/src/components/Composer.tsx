import { useState } from "react";
import type { KeyboardEvent } from "react";

import { LIMITS } from "../api/types";
import { Button } from "./Button";

interface Props {
  // true: el agente IA está atendiendo; contestar lo aparta.
  botActive: boolean;
  onSend: (text: string) => Promise<void>;
}

/**
 * Caja para contestar. Enter envía y Shift+Enter hace un salto de línea. Si el envío falla,
 * el texto se queda para poder reintentar.
 */
export function Composer({ botActive, onSend }: Props) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const clean = text.trim();
  const over = text.length > LIMITS.reply;
  const canSend = clean.length > 0 && !over && !busy;

  async function send() {
    if (!canSend) return;
    setBusy(true);
    try {
      await onSend(clean);
      setText("");
    } catch {
      // Quien llama ya avisó el error; el texto se queda.
    } finally {
      setBusy(false);
    }
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      void send();
    }
  }

  return (
    <form
      className="composer"
      onSubmit={(e) => {
        e.preventDefault();
        void send();
      }}
    >
      {botActive && (
        <p className="composer-note">Al contestar tú, el agente IA deja de responder en esta conversación.</p>
      )}
      <div className="composer-row">
        <textarea
          className="input textarea composer-input"
          aria-label="Escribe tu respuesta"
          placeholder="Escribe tu respuesta…"
          rows={2}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKeyDown}
        />
        <Button type="submit" icon="send" disabled={!canSend} busy={busy}>
          Enviar
        </Button>
      </div>
      <div className="composer-foot">
        <span>Enter envía · Shift + Enter, salto de línea</span>
        {text.length > LIMITS.reply - 500 && (
          <span className={over ? "field-count field-count-over" : "field-count"}>
            {text.length.toLocaleString("es")} / {LIMITS.reply.toLocaleString("es")}
          </span>
        )}
      </div>
    </form>
  );
}
