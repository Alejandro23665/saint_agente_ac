import { useState } from "react";
import type { FormEvent, ReactNode } from "react";

import { ApiError } from "../api/client";
import { Alert } from "./Alert";
import { Button } from "./Button";
import { TextField } from "./Field";
import { Modal } from "./Modal";

interface ConfirmDialogProps {
  title: string;
  children: ReactNode; // qué va a pasar, en palabras simples
  confirmLabel: string;
  onConfirm: () => Promise<void> | void;
  onClose: () => void;
  danger?: boolean;
  // Para lo que no se puede deshacer: hay que escribir este texto para poder confirmar.
  typeToConfirm?: string;
}

/**
 * "¿Seguro que...?" antes de borrar o desconectar. Mientras la acción corre no se puede
 * cerrar; si falla, el error se muestra aquí mismo y se puede reintentar.
 */
export function ConfirmDialog({ title, children, confirmLabel, onConfirm, onClose, danger = false, typeToConfirm }: ConfirmDialogProps) {
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ready = !typeToConfirm || typed.trim() === typeToConfirm.trim();

  async function confirm(event?: FormEvent) {
    event?.preventDefault();
    if (!ready || busy) return;
    setBusy(true);
    setError(null);
    try {
      await onConfirm();
      onClose();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Algo salió mal. Vuelve a intentar.");
      setBusy(false);
    }
  }

  return (
    <Modal
      title={title}
      onClose={onClose}
      dismissable={!busy}
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={busy}>
            Cancelar
          </Button>
          <Button variant={danger ? "danger" : "primary"} onClick={() => void confirm()} disabled={!ready} busy={busy}>
            {confirmLabel}
          </Button>
        </>
      }
    >
      <form className="stack" onSubmit={confirm}>
        {error && (
          <Alert kind="error" title="No se pudo hacer">
            {error}
          </Alert>
        )}
        <div className="muted">{children}</div>
        {typeToConfirm && (
          <TextField
            label={`Para confirmar, escribe: ${typeToConfirm}`}
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            autoComplete="off"
            data-autofocus
          />
        )}
      </form>
    </Modal>
  );
}
