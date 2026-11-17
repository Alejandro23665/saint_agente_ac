import { useRef, useState } from "react";
import type { ChangeEvent, FormEvent } from "react";

import type { KnowledgeUpload } from "../api/types";
import { LIMITS } from "../api/types";
import { readTextFile } from "../lib/knowledge";
import { errorText } from "../lib/useResource";
import { Alert } from "./Alert";
import { Button } from "./Button";
import { TextArea, TextField } from "./Field";
import { Modal } from "./Modal";

/**
 * Subir conocimiento: pegar un texto o cargar un .txt / .md. Hasta 200 000 caracteres.
 * Un título repetido reemplaza al documento anterior cuando el nuevo queda listo.
 */
export function UploadKnowledgeModal({
  onUpload,
  onClose,
}: {
  onUpload: (title: string, text: string) => Promise<KnowledgeUpload>;
  onClose: () => void;
}) {
  const picker = useRef<HTMLInputElement>(null);
  const [title, setTitle] = useState("");
  const [text, setText] = useState("");
  const [fileError, setFileError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const cleanTitle = title.trim();
  const tooLong = text.length > LIMITS.knowledgeChars;
  const ready = cleanTitle.length > 0 && text.trim().length > 0 && !tooLong;

  async function pick(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = ""; // para poder elegir el mismo archivo otra vez
    if (!file) return;
    setFileError(null);
    try {
      const loaded = await readTextFile(file);
      setText(loaded.text);
      setTitle((current) => current.trim() || loaded.title);
    } catch (e) {
      setFileError(e instanceof Error ? e.message : "No pudimos leer el archivo.");
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!ready || busy) return;
    setBusy(true);
    setError(null);
    try {
      await onUpload(cleanTitle, text);
      onClose();
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
    }
  }

  return (
    <Modal
      title="Subir conocimiento"
      wide
      onClose={onClose}
      dismissable={!busy}
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={busy}>
            Cancelar
          </Button>
          <Button type="submit" form="subir-conocimiento" busy={busy} disabled={!ready}>
            Subir
          </Button>
        </>
      }
    >
      <form id="subir-conocimiento" className="stack" onSubmit={(e) => void submit(e)}>
        <Alert kind="warn" title="Sube solo información pública del negocio">
          Horarios, productos, precios, políticas. El servicio gratuito que calcula los embeddings guarda lo que recibe: nunca pegues datos
          de clientes ni claves.
        </Alert>
        {error && (
          <Alert kind="error" title="No se pudo subir">
            {error}
          </Alert>
        )}
        {fileError && (
          <Alert kind="error" title="No pudimos usar ese archivo">
            {fileError}
          </Alert>
        )}

        <TextField
          label="Título"
          placeholder="Horarios y ubicación"
          hint="Si subes otro texto con el mismo título, reemplaza al anterior cuando el nuevo esté listo."
          value={title}
          maxLength={LIMITS.knowledgeTitle}
          onChange={(e) => setTitle(e.target.value)}
          data-autofocus
        />

        <div className="row">
          <Button variant="secondary" small icon="upload" onClick={() => picker.current?.click()}>
            Cargar un archivo .txt o .md
          </Button>
          <input
            ref={picker}
            type="file"
            className="visually-hidden"
            accept=".txt,.md,.markdown,text/plain,text/markdown"
            aria-label="Elegir un archivo de texto"
            tabIndex={-1}
            onChange={(e) => void pick(e)}
          />
          <span className="muted">o pega el texto aquí abajo</span>
        </div>

        <TextArea
          label="Texto"
          rows={12}
          counter
          limit={LIMITS.knowledgeChars}
          value={text}
          error={tooLong ? `Pasa del máximo de ${LIMITS.knowledgeChars.toLocaleString("es")} caracteres. Divídelo en varios documentos.` : null}
          onChange={(e) => setText(e.target.value)}
        />
      </form>
    </Modal>
  );
}
