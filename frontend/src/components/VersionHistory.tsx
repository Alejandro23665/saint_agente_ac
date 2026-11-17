import { useState } from "react";

import { agentApi } from "../api/agent";
import type { Agent, AgentVersion } from "../api/types";
import { dateTime } from "../lib/format";
import type { Resource } from "../lib/useResource";
import { Alert } from "./Alert";
import { Button } from "./Button";
import { ConfirmDialog } from "./ConfirmDialog";
import { EmptyState, Spinner, StatusPill } from "./Feedback";
import { Modal } from "./Modal";
import { useToast } from "./Toast";

function firstLine(text: string, max = 110): string {
  const flat = text.replace(/\s+/g, " ").trim();
  return flat.length > max ? `${flat.slice(0, max)}…` : flat;
}

/** Historial de instrucciones: quién las escribió y cuándo; ver el texto y volver a una anterior. */
export function VersionHistory({
  versions,
  canEdit,
  onRestored,
}: {
  versions: Resource<AgentVersion[]>;
  canEdit: boolean;
  onRestored: (agent: Agent) => void;
}) {
  const toast = useToast();
  const [viewing, setViewing] = useState<AgentVersion | null>(null);
  const [restoring, setRestoring] = useState<AgentVersion | null>(null);

  return (
    <section className="card" aria-labelledby="historial-titulo">
      <h2 id="historial-titulo">Historial de versiones</h2>
      <p className="muted">
        Cada vez que cambias las instrucciones se guarda una versión. Puedes ver cómo estaban y volver a una anterior: volver crea una
        versión nueva, no borra nada.
      </p>

      {versions.error && (
        <Alert kind="error" title="No pudimos cargar el historial">
          {versions.error}
        </Alert>
      )}
      {versions.loading ? (
        <Spinner label="Cargando el historial" />
      ) : !versions.data?.length ? (
        <EmptyState icon="history" title="Todavía no hay versiones" />
      ) : (
        <ul className="versions">
          {versions.data.map((v) => (
            <li key={v.version} className="version">
              <div className="version-main">
                <strong>Versión {v.version}</strong>
                {v.is_current && <StatusPill tone="success">Actual</StatusPill>}
                <small>
                  {v.created_by ? `${v.created_by} · ` : "De fábrica · "}
                  {dateTime(v.created_at)}
                </small>
                <p className="muted">{firstLine(v.instructions)}</p>
              </div>
              <div className="version-actions">
                <Button variant="secondary" small onClick={() => setViewing(v)} aria-label={`Ver la versión ${v.version}`}>
                  Ver
                </Button>
                {canEdit && !v.is_current && (
                  <Button small onClick={() => setRestoring(v)} aria-label={`Volver a la versión ${v.version}`}>
                    Volver a esta
                  </Button>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}

      {viewing && (
        <Modal
          title={`Versión ${viewing.version}`}
          wide
          onClose={() => setViewing(null)}
          footer={
            <>
              <Button variant="secondary" onClick={() => setViewing(null)}>
                Cerrar
              </Button>
              {canEdit && !viewing.is_current && (
                <Button
                  onClick={() => {
                    setRestoring(viewing);
                    setViewing(null);
                  }}
                >
                  Volver a esta versión
                </Button>
              )}
            </>
          }
        >
          <p className="muted">
            {viewing.created_by ? `Escrita por ${viewing.created_by}` : "Versión de fábrica"} · {dateTime(viewing.created_at)}
          </p>
          <pre className="version-text">{viewing.instructions}</pre>
        </Modal>
      )}

      {restoring && (
        <ConfirmDialog
          title={`Volver a la versión ${restoring.version}`}
          confirmLabel="Volver a esta versión"
          onClose={() => setRestoring(null)}
          onConfirm={async () => {
            const agent = await agentApi.restore(restoring.version);
            toast.success(`Ahora el agente usa el texto de la versión ${restoring.version}`);
            onRestored(agent);
          }}
        >
          El agente usará el texto de la versión {restoring.version} desde su siguiente respuesta. Se guarda como una versión nueva, así que
          la actual no se pierde. Si tienes cambios sin guardar en las instrucciones, se descartan.
        </ConfirmDialog>
      )}
    </section>
  );
}
