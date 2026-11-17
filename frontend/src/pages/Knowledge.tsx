import { useEffect, useState } from "react";

import { knowledgeApi } from "../api/knowledge";
import type { KnowledgeSource } from "../api/types";
import { useRole } from "../auth/session";
import { Alert } from "../components/Alert";
import { Button } from "../components/Button";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { EmptyState, Spinner, StatusPill } from "../components/Feedback";
import { KnowledgeDetail } from "../components/KnowledgeDetail";
import { useToast } from "../components/Toast";
import { UploadKnowledgeModal } from "../components/UploadKnowledgeModal";
import { useDashboardRefresh, useEvents } from "../layout/dashboard";
import { plural, timeAgo } from "../lib/format";
import { canRetry, sizeLine } from "../lib/knowledge";
import { KNOWLEDGE_STATUS, statusOf } from "../lib/status";
import { errorText, useResource } from "../lib/useResource";

// En milisegundos. Es un objeto para que las pruebas lo puedan acortar.
export const knowledgeTiming = { poll: 5000 };

type Dialog = { kind: "upload" } | { kind: "view"; source: KnowledgeSource } | { kind: "delete"; source: KnowledgeSource } | null;

export function Knowledge() {
  const { canEdit } = useRole();
  const toast = useToast();
  const refreshDashboard = useDashboardRefresh();
  const list = useResource(knowledgeApi.list);
  const [dialog, setDialog] = useState<Dialog>(null);
  const [retrying, setRetrying] = useState<string | null>(null);

  const sources = list.data ?? [];
  const processing = sources.some((s) => s.status === "processing");

  // Llega knowledge.changed por /events cuando un documento queda listo o falla.
  useEvents(["knowledge.changed", "ready"], () => void list.reload());

  // Por si /events no está disponible: mientras algo se procesa, se vuelve a preguntar.
  useEffect(() => {
    if (!processing) return;
    const timer = window.setInterval(() => void list.reload(), knowledgeTiming.poll);
    return () => window.clearInterval(timer);
  }, [processing, list.reload]);

  function changed() {
    void list.reload();
    refreshDashboard(["knowledge"]);
  }

  async function upload(title: string, text: string) {
    const result = await knowledgeApi.upload(title, text);
    toast.show(
      result.already_loaded
        ? "Ese mismo texto ya estaba cargado y listo. No se hizo nada."
        : "Se subió. Lo estamos procesando; en unos segundos estará listo.",
    );
    changed();
    return result;
  }

  async function retry(source: KnowledgeSource) {
    setRetrying(source.id);
    try {
      await knowledgeApi.retry(source.id);
      toast.show("Lo estamos procesando otra vez.");
      changed();
    } catch (e) {
      toast.error(errorText(e));
    } finally {
      setRetrying(null);
    }
  }

  const ready = sources.filter((s) => s.status === "ready").length;

  return (
    <main className="page">
      <div className="page-head page-head-row">
        <div>
          <h1>Conocimiento</h1>
          <p>Lo que el agente sabrá de tu negocio: horarios, productos, precios, políticas.</p>
        </div>
        {canEdit && (
          <Button icon="upload" onClick={() => setDialog({ kind: "upload" })} disabled={list.loading}>
            Subir conocimiento
          </Button>
        )}
      </div>

      <Alert kind="info" title="El agente todavía no usa este conocimiento">
        Por ahora los textos se guardan y se preparan para buscar. Que el agente los use al contestar llega con el bloque 4.5.
      </Alert>
      {!canEdit && !list.loading && (
        <Alert kind="info" title="Solo lectura">
          Subir, reintentar y borrar documentos lo hace el dueño o un administrador.
        </Alert>
      )}
      {list.error && (
        <Alert kind="error" title="No pudimos cargar el conocimiento">
          {list.error}{" "}
          <button type="button" className="link-button" onClick={() => void list.reload()}>
            Reintentar
          </button>
        </Alert>
      )}

      {list.loading ? (
        <div className="center-box">
          <Spinner label="Cargando el conocimiento" size={28} />
        </div>
      ) : sources.length === 0 && !list.error ? (
        <section className="card">
          <EmptyState
            icon="book"
            title="Todavía no subes nada"
            action={
              canEdit ? (
                <Button icon="upload" onClick={() => setDialog({ kind: "upload" })}>
                  Subir conocimiento
                </Button>
              ) : undefined
            }
          >
            {canEdit
              ? "Sube un texto con los horarios, los productos y los precios. Puedes pegarlo o cargar un archivo .txt o .md."
              : "Cuando el dueño o un administrador suba textos, aparecerán aquí."}
          </EmptyState>
        </section>
      ) : (
        sources.length > 0 && (
          <section className="card" aria-label="Documentos">
            <div className="card-head">
              <h2>{plural(sources.length, "documento")}</h2>
              <small>{ready} listos</small>
            </div>
            <ul className="docs">
              {sources.map((source) => {
                const status = statusOf(KNOWLEDGE_STATUS, source.status);
                return (
                  <li key={source.id} className="doc">
                    <div className="doc-main">
                      <div className="doc-title">
                        <strong>{source.title}</strong>
                        <StatusPill tone={status.tone} dot>
                          {status.label}
                        </StatusPill>
                      </div>
                      <small>
                        {sizeLine(source)}
                        {source.created_by ? ` · subido por ${source.created_by}` : ""} · {timeAgo(source.updated_at)}
                      </small>
                      {source.status === "failed" && (
                        <Alert kind="error" title="No se pudo procesar">
                          {source.error ?? "Ocurrió un error."} {canEdit && "Cuando se arregle, pulsa Reintentar: el texto no se pierde."}
                        </Alert>
                      )}
                      {source.status === "processing" && canRetry(source) && (
                        <p className="muted">Lleva varios minutos procesándose. Si no avanza, pulsa Reintentar.</p>
                      )}
                    </div>
                    <div className="doc-actions">
                      <Button variant="secondary" small onClick={() => setDialog({ kind: "view", source })} aria-label={`Ver los fragmentos de ${source.title}`}>
                        Ver fragmentos
                      </Button>
                      {canEdit && canRetry(source) && (
                        <Button small icon="refresh" onClick={() => void retry(source)} busy={retrying === source.id} aria-label={`Reintentar ${source.title}`}>
                          Reintentar
                        </Button>
                      )}
                      {canEdit && (
                        <Button
                          variant="ghost"
                          small
                          icon="trash"
                          className="danger-text"
                          onClick={() => setDialog({ kind: "delete", source })}
                          aria-label={`Borrar ${source.title}`}
                        >
                          Borrar
                        </Button>
                      )}
                    </div>
                  </li>
                );
              })}
            </ul>
          </section>
        )
      )}

      {dialog?.kind === "upload" && <UploadKnowledgeModal onUpload={upload} onClose={() => setDialog(null)} />}
      {dialog?.kind === "view" && <KnowledgeDetail source={dialog.source} onClose={() => setDialog(null)} />}
      {dialog?.kind === "delete" && (
        <ConfirmDialog
          title="Borrar el documento"
          confirmLabel="Borrar documento"
          danger
          onClose={() => setDialog(null)}
          onConfirm={async () => {
            await knowledgeApi.remove(dialog.source.id);
            toast.success("Documento borrado");
            changed();
          }}
        >
          Se borra "{dialog.source.title}" con todos sus fragmentos. El agente dejará de poder usarlo. Esto no se puede deshacer; si lo necesitas
          otra vez, tendrás que subirlo de nuevo.
        </ConfirmDialog>
      )}
    </main>
  );
}
