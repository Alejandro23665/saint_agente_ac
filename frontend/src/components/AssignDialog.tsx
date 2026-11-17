import { useState } from "react";

import { teamApi } from "../api/team";
import { roleLabel } from "../lib/format";
import { errorText, useResource } from "../lib/useResource";
import { Alert } from "./Alert";
import { Button } from "./Button";
import { SelectField } from "./Field";
import { Modal } from "./Modal";
import { Spinner } from "./Feedback";

/** Elegir a quién se asigna la conversación (o dejarla sin asignar). */
export function AssignDialog({
  currentUserId,
  onAssign,
  onClose,
}: {
  currentUserId: string | null;
  onAssign: (userId: string | null) => Promise<void>;
  onClose: () => void;
}) {
  const members = useResource(teamApi.members);
  const [choice, setChoice] = useState(currentUserId ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    setBusy(true);
    setError(null);
    try {
      await onAssign(choice || null);
      onClose();
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
    }
  }

  return (
    <Modal
      title="Asignar la conversación"
      onClose={onClose}
      dismissable={!busy}
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={busy}>
            Cancelar
          </Button>
          <Button onClick={() => void save()} busy={busy} disabled={!members.data}>
            Asignar
          </Button>
        </>
      }
    >
      {error && (
        <Alert kind="error" title="No se pudo asignar">
          {error}
        </Alert>
      )}
      {members.error && (
        <Alert kind="error" title="No pudimos cargar al equipo">
          {members.error}
        </Alert>
      )}
      {members.loading ? (
        <Spinner label="Cargando al equipo" />
      ) : (
        members.data && (
          <SelectField label="Quién la atiende" value={choice} onChange={(e) => setChoice(e.target.value)} data-autofocus>
            <option value="">Sin asignar</option>
            {members.data.map((m) => (
              <option key={m.user_id} value={m.user_id}>
                {m.full_name} ({roleLabel(m.role)}){m.is_me ? " · tú" : ""}
              </option>
            ))}
          </SelectField>
        )
      )}
    </Modal>
  );
}
