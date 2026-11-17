import { useState } from "react";

import { numbersApi } from "../api/numbers";
import type { WhatsappNumber } from "../api/types";
import { LIMITS } from "../api/types";
import { useRole } from "../auth/session";
import { Alert } from "../components/Alert";
import { Button } from "../components/Button";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { ConnectNumberModal } from "../components/ConnectNumberModal";
import { EmptyState, Spinner, StatusPill } from "../components/Feedback";
import { Switch } from "../components/Field";
import { Icon } from "../components/Icon";
import { useToast } from "../components/Toast";
import { useDashboardRefresh, useEvents } from "../layout/dashboard";
import { dateTime, phone as formatPhone, timeAgo } from "../lib/format";
import { NUMBER_STATUS, statusOf } from "../lib/status";
import { errorText, useResource } from "../lib/useResource";

type Dialog =
  | { kind: "connect"; number: WhatsappNumber | null }
  | { kind: "disconnect"; number: WhatsappNumber }
  | { kind: "delete"; number: WhatsappNumber }
  | null;

function title(n: WhatsappNumber): string {
  return n.phone ? formatPhone(n.phone) : "Número sin vincular";
}

function connectionLine(n: WhatsappNumber): string {
  if (n.status === "open") return n.last_connected_at ? `Conectado desde el ${dateTime(n.last_connected_at)}` : "Conectado";
  if (n.last_connected_at) return `Última conexión: ${timeAgo(n.last_connected_at)}`;
  return "Todavía no se conectó";
}

function NumberCard({
  number,
  canEdit,
  isOwner,
  busy,
  onCheck,
  onGroups,
  onDialog,
}: {
  number: WhatsappNumber;
  canEdit: boolean;
  isOwner: boolean;
  busy: boolean;
  onCheck: () => void;
  onGroups: (enabled: boolean) => void;
  onDialog: (dialog: Exclude<Dialog, null>) => void;
}) {
  const status = statusOf(NUMBER_STATUS, number.status);
  const connected = number.status === "open";
  const needsQr = !connected && number.status !== "banned";

  return (
    <article className="card number-card" aria-label={title(number)}>
      <div className="number-card-head">
        <span className="icon-box">
          <Icon name="phone" />
        </span>
        <div className="number-card-title">
          <h2>{title(number)}</h2>
          <small>{connectionLine(number)}</small>
        </div>
        <StatusPill tone={status.tone} dot>
          {status.label}
        </StatusPill>
      </div>

      {number.status === "banned" && (
        <Alert kind="error" title="WhatsApp bloqueó este número">
          Saint ya no puede usarlo. Revisa tu cuenta de WhatsApp desde el teléfono.
        </Alert>
      )}
      {number.status === "close" && number.last_connected_at && (
        <Alert kind="warn" title="El teléfono se desconectó">
          Mientras tanto el agente no puede contestar. Vuelve a vincularlo con un QR.
        </Alert>
      )}

      <Switch
        label="Mensajes de grupos"
        hint="Si está apagado, el agente solo atiende conversaciones directas."
        checked={number.groups_enabled}
        onChange={onGroups}
        disabled={!canEdit || busy}
      />

      <div className="card-actions">
        <Button variant="secondary" small icon="refresh" onClick={onCheck} busy={busy}>
          Revisar estado
        </Button>
        {canEdit && needsQr && (
          <Button small icon="phone" onClick={() => onDialog({ kind: "connect", number })}>
            Mostrar QR
          </Button>
        )}
        {canEdit && connected && (
          <Button variant="secondary" small onClick={() => onDialog({ kind: "disconnect", number })}>
            Desconectar
          </Button>
        )}
        {isOwner && (
          <Button variant="ghost" small icon="trash" className="danger-text" onClick={() => onDialog({ kind: "delete", number })}>
            Borrar
          </Button>
        )}
      </div>
    </article>
  );
}

export function Numbers() {
  const { canEdit, isOwner } = useRole();
  const toast = useToast();
  const refreshDashboard = useDashboardRefresh();
  const list = useResource(numbersApi.list);
  const [dialog, setDialog] = useState<Dialog>(null);
  const [working, setWorking] = useState<string | null>(null);

  useEvents(["numbers.changed", "ready"], () => void list.reload());

  const numbers = list.data ?? [];
  const full = numbers.length >= LIMITS.numbers;

  function replace(updated: WhatsappNumber) {
    list.setData((current) => current?.map((n) => (n.id === updated.id ? updated : n)) ?? current);
    refreshDashboard(["numbers"]);
  }

  async function check(n: WhatsappNumber) {
    setWorking(n.id);
    try {
      const fresh = await numbersApi.refresh(n.id);
      replace(fresh);
      toast.show(`${title(fresh)}: ${statusOf(NUMBER_STATUS, fresh.status).label.toLowerCase()}`);
    } catch (e) {
      toast.error(errorText(e));
    } finally {
      setWorking(null);
    }
  }

  async function setGroups(n: WhatsappNumber, enabled: boolean) {
    setWorking(n.id);
    try {
      replace(await numbersApi.setGroups(n.id, enabled));
      toast.success(enabled ? "Ahora recibes mensajes de grupos" : "Se apagaron los mensajes de grupos");
    } catch (e) {
      toast.error(errorText(e));
    } finally {
      setWorking(null);
    }
  }

  function changed() {
    void list.reload();
    refreshDashboard(["numbers"]);
  }

  return (
    <main className="page">
      <div className="page-head page-head-row">
        <div>
          <h1>Números de WhatsApp</h1>
          <p>Los números que atiende tu agente. Puedes tener hasta {LIMITS.numbers}.</p>
        </div>
        {canEdit && (
          <Button icon="plus" onClick={() => setDialog({ kind: "connect", number: null })} disabled={full || list.loading}>
            Conectar un número
          </Button>
        )}
      </div>

      {full && canEdit && (
        <Alert kind="info" title="Llegaste al límite">
          Un negocio puede tener hasta {LIMITS.numbers} números. Borra uno para conectar otro.
        </Alert>
      )}
      {!canEdit && !list.loading && (
        <Alert kind="info" title="Solo lectura">
          Conectar, desconectar y cambiar los números lo hace el dueño o un administrador.
        </Alert>
      )}
      {list.error && (
        <Alert kind="error" title="No pudimos cargar los números">
          {list.error}{" "}
          <button type="button" className="link-button" onClick={() => void list.reload()}>
            Reintentar
          </button>
        </Alert>
      )}

      {list.loading ? (
        <div className="center-box">
          <Spinner label="Cargando los números" size={28} />
        </div>
      ) : numbers.length === 0 && !list.error ? (
        <section className="card">
          <EmptyState
            icon="phone"
            title="Todavía no conectas un número"
            action={
              canEdit ? (
                <Button icon="plus" onClick={() => setDialog({ kind: "connect", number: null })}>
                  Conectar un número
                </Button>
              ) : undefined
            }
          >
            {canEdit
              ? "Conéctalo con un QR para que tu agente empiece a responder."
              : "Pídele al dueño o a un administrador que conecte el número del negocio."}
          </EmptyState>
        </section>
      ) : (
        <div className="number-cards">
          {numbers.map((n) => (
            <NumberCard
              key={n.id}
              number={n}
              canEdit={canEdit}
              isOwner={isOwner}
              busy={working === n.id}
              onCheck={() => void check(n)}
              onGroups={(enabled) => void setGroups(n, enabled)}
              onDialog={setDialog}
            />
          ))}
        </div>
      )}

      {dialog?.kind === "connect" && (
        <ConnectNumberModal existing={dialog.number} onClose={() => setDialog(null)} onChanged={changed} />
      )}

      {dialog?.kind === "disconnect" && (
        <ConfirmDialog
          title="Desconectar el número"
          confirmLabel="Desconectar"
          onClose={() => setDialog(null)}
          onConfirm={async () => {
            replace(await numbersApi.disconnect(dialog.number.id));
            toast.success("El número se desconectó");
          }}
        >
          Se desvincula {title(dialog.number)} del WhatsApp del teléfono. Tus conversaciones se conservan y el agente deja de
          contestar hasta que lo vuelvas a conectar con un QR.
        </ConfirmDialog>
      )}

      {dialog?.kind === "delete" && (
        <ConfirmDialog
          title="Borrar el número"
          confirmLabel="Borrar número"
          danger
          typeToConfirm={dialog.number.phone ? formatPhone(dialog.number.phone) : "BORRAR"}
          onClose={() => setDialog(null)}
          onConfirm={async () => {
            await numbersApi.remove(dialog.number.id);
            list.setData((current) => current?.filter((n) => n.id !== dialog.number.id) ?? current);
            refreshDashboard(["numbers"]);
            toast.success("El número se borró");
          }}
        >
          Se borra de Saint y de WhatsApp: el agente deja de recibir sus mensajes. Las conversaciones y los mensajes que
          ya tienes se conservan. Esto no se puede deshacer.
        </ConfirmDialog>
      )}
    </main>
  );
}
