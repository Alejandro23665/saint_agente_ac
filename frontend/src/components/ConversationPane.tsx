/**
 * La conversación elegida en la bandeja: quién es, quién la atiende, los mensajes y la caja
 * para contestar. Acciones: tomarla (el agente IA deja de contestar), devolverla al agente,
 * asignarla (dueño y administrador) y cerrarla. Se pone al día sola con los avisos de /events.
 */
import { useState } from "react";
import { Link } from "react-router-dom";

import { inboxApi } from "../api/inbox";
import { useAuth, useRole } from "../auth/session";
import { useDashboardRefresh, useEvents } from "../layout/dashboard";
import { phone as formatPhone, timeAgo } from "../lib/format";
import { contactName } from "../lib/inbox";
import { MODE, statusOf } from "../lib/status";
import { errorText, useResource } from "../lib/useResource";
import { useMessages } from "../lib/useMessages";
import { initials } from "../auth/session";
import { Alert } from "./Alert";
import { AssignDialog } from "./AssignDialog";
import { Button } from "./Button";
import { Composer } from "./Composer";
import { ConfirmDialog } from "./ConfirmDialog";
import { Spinner, StatusPill } from "./Feedback";
import { Icon } from "./Icon";
import { MessageThread } from "./MessageThread";
import { useToast } from "./Toast";

type Dialog = "assign" | "close" | null;

export function ConversationPane({ id, backTo }: { id: string; backTo: string }) {
  const { session } = useAuth();
  const { canEdit } = useRole();
  const toast = useToast();
  const refreshDashboard = useDashboardRefresh();
  const conversation = useResource(() => inboxApi.get(id), [id]);
  const thread = useMessages(id);
  const [dialog, setDialog] = useState<Dialog>(null);
  const [acting, setActing] = useState<string | null>(null);
  const me = session?.user.id ?? "";
  const c = conversation.data;

  async function refreshAll() {
    await Promise.all([conversation.reload(), thread.reload()]);
    refreshDashboard(["conversations"]);
  }

  useEvents(["conversation.changed"], (event) => {
    if (!event.conversation_id || event.conversation_id === id) void refreshAll();
  });
  // inbox.changed: por ejemplo un mensaje que pasó a enviado o entregado. "ready": al reconectar.
  useEvents(["inbox.changed", "ready"], () => void refreshAll());

  async function act(name: string, run: () => Promise<unknown>, done: string) {
    setActing(name);
    try {
      await run();
      toast.success(done);
      await refreshAll();
    } catch (e) {
      toast.error(errorText(e));
    } finally {
      setActing(null);
    }
  }

  async function send(text: string) {
    try {
      await inboxApi.reply(id, text);
    } catch (e) {
      toast.error(errorText(e));
      throw e;
    }
    await refreshAll();
  }

  const open = c?.status === "open";
  const mine = c?.assigned?.user_id === me;
  const showTake = open && !(c?.mode === "human" && mine);
  const showRelease = open && c?.mode !== "bot";

  return (
    <section className="pane" aria-label="Conversación">
      <Link to={backTo} className="pane-back">
        <Icon name="chevronRight" size={16} className="flip" /> Volver a la lista
      </Link>

      {conversation.loading && (
        <div className="center-box">
          <Spinner label="Cargando la conversación" size={28} />
        </div>
      )}
      {conversation.error && !c && (
        <div className="pane-pad">
          <Alert kind="error" title="No pudimos abrir la conversación">
            {conversation.error}
          </Alert>
        </div>
      )}

      {c && (
        <>
          <header className="pane-head">
            <span className="avatar">{initials(contactName(c))}</span>
            <div className="pane-title">
              <h2>{contactName(c)}</h2>
              <small>
                {c.contact.phone ? formatPhone(c.contact.phone) : "Sin teléfono"}
                {c.number_phone ? ` · por ${formatPhone(c.number_phone)}` : ""}
              </small>
            </div>
            <StatusPill tone={statusOf(MODE, c.mode).tone}>{statusOf(MODE, c.mode).label}</StatusPill>
            {!open && <StatusPill tone="muted">Cerrada</StatusPill>}
          </header>

          <div className="pane-sub">
            <p className="muted">
              {!open
                ? "Esta conversación está cerrada."
                : c.mode === "bot"
                  ? "La atiende el agente IA."
                  : c.assigned
                    ? mine
                      ? "La atiendes tú."
                      : `La atiende ${c.assigned.name ?? "alguien del equipo"}.`
                    : "La atiende una persona, sin asignar."}{" "}
              {c.last_message_at ? `Último mensaje ${timeAgo(c.last_message_at)}.` : ""}
            </p>
            {open && (
              <div className="pane-actions">
                {showTake && (
                  <Button small onClick={() => void act("take", () => inboxApi.take(id), "Tomaste la conversación")} busy={acting === "take"} disabled={acting !== null}>
                    Tomar
                  </Button>
                )}
                {showRelease && (
                  <Button
                    small
                    variant="secondary"
                    onClick={() => void act("release", () => inboxApi.release(id), "La conversación volvió al agente IA")}
                    busy={acting === "release"}
                    disabled={acting !== null}
                  >
                    Devolver al agente
                  </Button>
                )}
                {canEdit && (
                  <Button small variant="secondary" onClick={() => setDialog("assign")} disabled={acting !== null}>
                    Asignar
                  </Button>
                )}
                <Button small variant="ghost" className="danger-text" onClick={() => setDialog("close")} disabled={acting !== null}>
                  Cerrar
                </Button>
              </div>
            )}
          </div>

          <MessageThread list={thread} />

          {open ? (
            <Composer botActive={c.mode === "bot"} onSend={send} />
          ) : (
            <p className="composer-closed">Si el cliente vuelve a escribir, se abre una conversación nueva.</p>
          )}
        </>
      )}

      {dialog === "assign" && c && (
        <AssignDialog
          currentUserId={c.assigned?.user_id ?? null}
          onClose={() => setDialog(null)}
          onAssign={async (userId) => {
            await inboxApi.assign(id, userId);
            toast.success(userId ? "Conversación asignada" : "La conversación quedó sin asignar");
            await refreshAll();
          }}
        />
      )}
      {dialog === "close" && (
        <ConfirmDialog
          title="Cerrar la conversación"
          confirmLabel="Cerrar conversación"
          onClose={() => setDialog(null)}
          onConfirm={async () => {
            await inboxApi.close(id);
            toast.success("Conversación cerrada");
            await refreshAll();
          }}
        >
          Sale de las abiertas. Si el cliente vuelve a escribir, se abre una conversación nueva. La puedes seguir viendo en
          "Cerradas".
        </ConfirmDialog>
      )}
    </section>
  );
}
