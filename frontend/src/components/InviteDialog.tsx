import { useRef, useState } from "react";
import type { FormEvent } from "react";

import { teamApi } from "../api/team";
import type { NewInvitation, Role } from "../api/types";
import { dateTime, roleLabel } from "../lib/format";
import { invitableRoles, ROLE_HINTS } from "../lib/permissions";
import { errorText } from "../lib/useResource";
import { Alert } from "./Alert";
import { Button } from "./Button";
import { SelectField, TextField } from "./Field";
import { Modal } from "./Modal";
import { useToast } from "./Toast";

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

/** Copia al portapapeles; si el navegador no deja, selecciona el texto para copiarlo a mano. */
async function copyText(text: string, input: HTMLInputElement | null): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    input?.focus();
    input?.select();
    return false;
  }
}

/**
 * Invitar a alguien: correo y rol. Saint no manda correos; la API devuelve un enlace de un
 * solo uso (vence en 72 horas) que se muestra UNA vez, para que lo compartas tú.
 */
export function InviteDialog({
  myRole,
  onInvited,
  onClose,
}: {
  myRole: Role;
  onInvited: () => void;
  onClose: () => void;
}) {
  const toast = useToast();
  const roles = invitableRoles(myRole);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>(roles[0] ?? "agent");
  const [emailError, setEmailError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [invitation, setInvitation] = useState<NewInvitation | null>(null);
  const [copied, setCopied] = useState(false);
  const linkBox = useRef<HTMLInputElement>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    const clean = email.trim();
    if (!EMAIL.test(clean)) {
      setEmailError("Escribe un correo completo, por ejemplo nombre@negocio.com");
      return;
    }
    setEmailError(null);
    setError(null);
    setBusy(true);
    try {
      setInvitation(await teamApi.invite(clean, role));
      onInvited();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  async function copy() {
    if (!invitation) return;
    const ok = await copyText(invitation.link, linkBox.current);
    setCopied(ok);
    if (ok) toast.success("Enlace copiado");
    else toast.error("No pudimos copiarlo solo. Selecciónalo y cópialo a mano.");
  }

  if (invitation) {
    return (
      <Modal
        title="Invitación lista"
        onClose={onClose}
        footer={<Button onClick={onClose}>Listo, ya lo copié</Button>}
      >
        <p>
          Comparte este enlace con <strong>{invitation.email}</strong>. Entrará como <strong>{roleLabel(invitation.role)}</strong>.
        </p>
        <div className="row">
          <input
            ref={linkBox}
            className="input"
            style={{ flex: 1, minWidth: 220 }}
            readOnly
            aria-label="Enlace de invitación"
            value={invitation.link}
            onFocus={(e) => e.currentTarget.select()}
          />
          <Button icon={copied ? "check" : "copy"} onClick={() => void copy()}>
            {copied ? "Copiado" : "Copiar"}
          </Button>
        </div>
        <Alert kind="warn" title="Este enlace se muestra solo ahora">
          Saint no lo guarda ni envía correos. Sirve una sola vez y vence el {dateTime(invitation.expires_at)} (72 horas). Si lo pierdes,
          vuelve a invitar a la misma persona: el enlace anterior deja de servir.
        </Alert>
      </Modal>
    );
  }

  return (
    <Modal
      title="Invitar a alguien"
      onClose={onClose}
      dismissable={!busy}
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={busy}>
            Cancelar
          </Button>
          <Button type="submit" form="invitar" busy={busy}>
            Crear enlace
          </Button>
        </>
      }
    >
      <form id="invitar" className="stack" onSubmit={(e) => void submit(e)} noValidate>
        {error && (
          <Alert kind="error" title="No se pudo invitar">
            {error}
          </Alert>
        )}
        <TextField
          label="Correo"
          type="email"
          autoComplete="off"
          placeholder="nombre@negocio.com"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          error={emailError}
          data-autofocus
        />
        <SelectField
          label="Rol"
          value={role}
          onChange={(e) => setRole(e.target.value as Role)}
          hint={ROLE_HINTS[role]}
          disabled={roles.length < 2}
        >
          {roles.map((r) => (
            <option key={r} value={r}>
              {roleLabel(r)}
            </option>
          ))}
        </SelectField>
        {myRole === "admin" && <p className="muted">Como administrador, solo puedes invitar agentes.</p>}
      </form>
    </Modal>
  );
}
