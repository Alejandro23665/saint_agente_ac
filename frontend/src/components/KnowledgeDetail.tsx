import { knowledgeApi } from "../api/knowledge";
import type { KnowledgeSource } from "../api/types";
import { sizeLine } from "../lib/knowledge";
import { KNOWLEDGE_STATUS, statusOf } from "../lib/status";
import { useResource } from "../lib/useResource";
import { Alert } from "./Alert";
import { Button } from "./Button";
import { Spinner, StatusPill } from "./Feedback";
import { Modal } from "./Modal";

/** Los fragmentos de un documento, tal como los verá el agente al buscar. */
export function KnowledgeDetail({ source, onClose }: { source: KnowledgeSource; onClose: () => void }) {
  const detail = useResource(() => knowledgeApi.get(source.id), [source.id]);
  const status = statusOf(KNOWLEDGE_STATUS, source.status);

  return (
    <Modal
      title={source.title}
      wide
      onClose={onClose}
      footer={
        <Button variant="secondary" onClick={onClose}>
          Cerrar
        </Button>
      }
    >
      <div className="row">
        <StatusPill tone={status.tone}>{status.label}</StatusPill>
        <span className="muted">{sizeLine(source)}</span>
      </div>
      <p className="muted">
        El texto se parte en fragmentos de unos 500 tokens (más o menos 350 palabras). Cuando el agente use el conocimiento, buscará entre
        ellos el que más se parezca a la pregunta del cliente.
      </p>
      {detail.error && (
        <Alert kind="error" title="No pudimos cargar los fragmentos">
          {detail.error}
        </Alert>
      )}
      {detail.loading ? (
        <Spinner label="Cargando los fragmentos" />
      ) : (
        <ol className="chunks">
          {detail.data?.chunks.map((chunk) => (
            <li key={chunk.index} className="chunk">
              <div className="chunk-head">
                <strong>Fragmento {chunk.index + 1}</strong>
                <small>
                  {chunk.token_count ? `${chunk.token_count.toLocaleString("es")} tokens · ` : ""}
                  {chunk.has_embedding ? "listo para buscar" : "todavía sin procesar"}
                </small>
              </div>
              <pre className="version-text">{chunk.content}</pre>
            </li>
          ))}
          {detail.data && detail.data.chunks.length === 0 && <li className="muted">Este documento no tiene fragmentos.</li>}
        </ol>
      )}
    </Modal>
  );
}
