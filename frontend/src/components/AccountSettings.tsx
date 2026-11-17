import { useState } from "react";
import type { FormEvent } from "react";

import { accountApi } from "../api/account";
import { ApiError, refreshSession } from "../api/client";
import { LIMITS } from "../api/types";
import { useAuth } from "../auth/session";
import { errorText } from "../lib/useResource";
import { Alert } from "./Alert";
import { Button } from "./Button";
import { ConfirmDialog } from "./ConfirmDialog";
import { TextField } from "./Field";
import { useToast } from "./Toast";

/** Mi nombre (lo ve mi equipo). El correo no se cambia desde aquí. */
function ProfileCard() {
  const { session } = useAuth();
  const toast = useToast();
  const user = session?.user;
  const [name, setName] = useState(user?.full_name ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!user) return null;
  const dirty = name.trim().replace(/\s+/g, " ") !== user.full_name;

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!dirty || !name.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      await accountApi.updateProfile(name);
      await refreshSession(session?.active_tenant?.id ?? null); // el menú muestra el nombre nuevo
      toast.success("Tu nombre se guardó");
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card" aria-labelledby="perfil-titulo">
      <h2 id="perfil-titulo">Mi cuenta</h2>
      {error && (
        <Alert kind="error" title="No se pudo guardar">
          {error}
        </Alert>
      )}
      <form className="stack" onSubmit={(e) => void save(e)}>
        <TextField
          label="Mi nombre"
          hint="Es como te ve tu equipo en las conversaciones."
          value={name}
          maxLength={LIMITS.personName}
          onChange={(e) => setName(e.target.value)}
          autoComplete="name"
        />
        <TextField label="Correo" value={user.email} readOnly hint="Con este correo entras a Saint. Para cambiarlo, escríbenos." />
        <div className="row row-end">
          <Button type="submit" busy={busy} disabled={!dirty || !name.trim()}>
            Guardar mi nombre
          </Button>
        </div>
      </form>
    </section>
  );
}

/** Cambiar la contraseña. Las demás sesiones (otros navegadores o teléfonos) se cierran. */
function PasswordCard() {
  const toast = useToast();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [repeat, setRepeat] = useState("");
  const [show, setShow] = useState(false);
  const [errors, setErrors] = useState<{ current?: string; next?: string; repeat?: string }>({});
  const [problem, setProblem] = useState<{ kind: "error" | "warn"; title: string; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    const found: typeof errors = {};
    if (!current) found.current = "Escribe tu contraseña actual.";
    if (next.length < LIMITS.password) found.next = `Usa al menos ${LIMITS.password} caracteres.`;
    else if (next === current) found.next = "La nueva debe ser distinta de la actual.";
    else if (repeat !== next) found.repeat = "Las dos contraseñas no coinciden.";
    setErrors(found);
    if (Object.keys(found).length) return;

    setProblem(null);
    setBusy(true);
    try {
      const { closed_sessions: closed } = await accountApi.changePassword(current, next);
      setCurrent("");
      setNext("");
      setRepeat("");
      toast.success(
        closed > 0
          ? `Contraseña cambiada. Se cerró la sesión en ${closed === 1 ? "1 dispositivo más" : `${closed} dispositivos más`}.`
          : "Contraseña cambiada. No había otras sesiones abiertas.",
      );
    } catch (e) {
      if (e instanceof ApiError && e.status === 429) {
        setProblem({ kind: "warn", title: "Espera unos minutos", text: "Hubo muchos intentos seguidos. Por seguridad, vuelve a probar en 15 minutos." });
      } else if (e instanceof ApiError && e.status === 422 && /actual no es correcta/i.test(e.message)) {
        setErrors({ current: e.message });
      } else {
        setProblem({ kind: "error", title: "No se pudo cambiar la contraseña", text: errorText(e) });
      }
    } finally {
      setBusy(false);
    }
  }

  const type = show ? "text" : "password";
  return (
    <section className="card" aria-labelledby="clave-titulo">
      <h2 id="clave-titulo">Cambiar mi contraseña</h2>
      <p className="muted">
        Al cambiarla, se cierra tu sesión en los demás navegadores y teléfonos; en este seguirás dentro. Es lo que conviene si sospechas que alguien
        más tuvo acceso.
      </p>
      {problem && (
        <Alert kind={problem.kind} title={problem.title}>
          {problem.text}
        </Alert>
      )}
      <form className="stack" onSubmit={(e) => void submit(e)} noValidate>
        <TextField
          label="Contraseña actual"
          type={type}
          autoComplete="current-password"
          value={current}
          onChange={(e) => setCurrent(e.target.value)}
          error={errors.current}
        />
        <TextField
          label="Contraseña nueva"
          type={type}
          autoComplete="new-password"
          hint={`Mínimo ${LIMITS.password} caracteres.`}
          value={next}
          onChange={(e) => setNext(e.target.value)}
          error={errors.next}
        />
        <TextField
          label="Repite la contraseña nueva"
          type={type}
          autoComplete="new-password"
          value={repeat}
          onChange={(e) => setRepeat(e.target.value)}
          error={errors.repeat}
        />
        <label className="check" htmlFor="ver-claves">
          <input id="ver-claves" type="checkbox" checked={show} onChange={(e) => setShow(e.target.checked)} />
          Mostrar las contraseñas
        </label>
        <div className="row row-end">
          <Button type="submit" busy={busy} disabled={!current && !next && !repeat}>
            Cambiar contraseña
          </Button>
        </div>
      </form>
    </section>
  );
}

/** Cerrar sesión en todos los lados, por si se perdió un teléfono o se usó un equipo ajeno. */
function SessionsCard() {
  const { logout } = useAuth();
  const [confirm, setConfirm] = useState(false);

  return (
    <section className="card" aria-labelledby="sesiones-titulo">
      <h2 id="sesiones-titulo">Sesiones abiertas</h2>
      <p className="muted">
        Tu sesión dura hasta 7 días en cada navegador o teléfono. Si perdiste un equipo o entraste desde uno ajeno, ciérralas todas: tendrás que volver
        a iniciar sesión, también aquí.
      </p>
      <div>
        <Button variant="secondary" icon="logout" className="danger-text" onClick={() => setConfirm(true)}>
          Cerrar sesión en todos los dispositivos
        </Button>
      </div>
      {confirm && (
        <ConfirmDialog
          title="Cerrar todas las sesiones"
          confirmLabel="Cerrar todas"
          danger
          onClose={() => setConfirm(false)}
          onConfirm={async () => {
            await accountApi.logoutAll();
            await logout();
          }}
        >
          Se cierra tu sesión en todos los navegadores y teléfonos, incluido este. Tus conversaciones y tu equipo no cambian.
        </ConfirmDialog>
      )}
    </section>
  );
}

export function AccountSettings() {
  return (
    <>
      <ProfileCard />
      <PasswordCard />
      <SessionsCard />
    </>
  );
}
