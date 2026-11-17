/**
 * Aceptar una invitación: /invitacion#<token> (el enlace que se copia en Equipo).
 *
 * El token va después del # para que no viaje al servidor en la dirección ni quede en
 * los registros. Al abrir la página se lee una vez y se quita de la barra de direcciones.
 * - Sin cuenta en Saint: se pide nombre y contraseña nueva.
 * - Con cuenta: se pide su contraseña de siempre.
 * Al aceptar, la persona queda dentro con ese negocio elegido.
 */
import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";

import { invitationApi } from "../api/auth";
import { ApiError } from "../api/client";
import type { InvitationPreview } from "../api/types";
import { LIMITS } from "../api/types";
import { useAuth } from "../auth/session";
import { Alert } from "../components/Alert";
import { Button } from "../components/Button";
import { Spinner } from "../components/Feedback";
import { TextField } from "../components/Field";
import { Icon } from "../components/Icon";
import { Logo } from "../components/Logo";
import { dateTime, roleLabel } from "../lib/format";

type State =
  | { kind: "loading" }
  | { kind: "invalid" }
  | { kind: "offline"; message: string }
  | { kind: "ready"; preview: InvitationPreview };

type Problem = { kind: "error" | "warn"; title: string; text: string } | null;

const MIN_TOKEN = 20;

function problemFor(error: unknown): Problem {
  if (error instanceof ApiError && error.status === 401) {
    return { kind: "error", title: "La contraseña no es correcta", text: "Escribe la contraseña con la que ya entras a Saint." };
  }
  if (error instanceof ApiError && error.status === 429) {
    return { kind: "warn", title: "Espera unos minutos", text: "Hubo muchos intentos seguidos. Vuelve a probar en 15 minutos." };
  }
  return {
    kind: "error",
    title: "No pudimos aceptar la invitación",
    text: error instanceof ApiError ? error.message : "Algo salió mal. Vuelve a intentar.",
  };
}

export function AcceptInvitation() {
  const location = useLocation();
  const navigate = useNavigate();
  const { session, acceptInvitation } = useAuth();
  // Se lee una sola vez: enseguida se quita de la dirección.
  const [token] = useState(() => decodeURIComponent(location.hash.replace(/^#/, "")).trim());
  const [state, setState] = useState<State>({ kind: "loading" });
  const [attempt, setAttempt] = useState(0);

  const [fullName, setFullName] = useState("");
  const [password, setPassword] = useState("");
  const [repeat, setRepeat] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [remember, setRemember] = useState(true);
  const [errors, setErrors] = useState<{ name?: string; password?: string; repeat?: string }>({});
  const [problem, setProblem] = useState<Problem>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (location.hash) navigate("/invitacion", { replace: true });
  }, [location.hash, navigate]);

  useEffect(() => {
    if (token.length < MIN_TOKEN) {
      setState({ kind: "invalid" });
      return;
    }
    let alive = true;
    setState({ kind: "loading" });
    invitationApi
      .preview(token)
      .then((preview) => alive && setState({ kind: "ready", preview }))
      .catch((error: unknown) => {
        if (!alive) return;
        if (error instanceof ApiError && (error.status === 404 || error.status === 422)) setState({ kind: "invalid" });
        else setState({ kind: "offline", message: error instanceof ApiError ? error.message : "Algo salió mal." });
      });
    return () => {
      alive = false;
    };
  }, [token, attempt]);

  async function submit(event: FormEvent, preview: InvitationPreview) {
    event.preventDefault();
    if (busy) return;
    const found: typeof errors = {};
    if (!preview.has_account) {
      if (!fullName.trim()) found.name = "Escribe tu nombre, así te verá tu equipo.";
      if (password.length < LIMITS.password) found.password = `Usa al menos ${LIMITS.password} caracteres.`;
      else if (password !== repeat) found.repeat = "Las dos contraseñas no coinciden.";
    } else if (!password) {
      found.password = "Escribe tu contraseña.";
    }
    setErrors(found);
    if (Object.keys(found).length) return;

    setProblem(null);
    setBusy(true);
    try {
      await acceptInvitation({
        token,
        password,
        full_name: preview.has_account ? null : fullName.trim(),
        remember,
      });
      navigate("/", { replace: true });
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) {
        setState({ kind: "invalid" });
      } else {
        setProblem(problemFor(error));
      }
      setBusy(false);
    }
  }

  return (
    <div className="login">
      <main className="login-main">
        <Logo width={150} />
        <div className="login-body">
          {state.kind === "loading" && (
            <div className="stack">
              <Spinner label="Revisando la invitación" />
              <p className="muted">Revisando la invitación…</p>
            </div>
          )}

          {state.kind === "invalid" && (
            <div className="stack">
              <h1>Esta invitación ya no sirve</h1>
              <p className="login-lead">
                El enlace venció (dura 72 horas), ya se usó o lo anularon. Pídele a quien te invitó que te envíe uno
                nuevo desde Equipo.
              </p>
              <Link to="/">{session ? "Ir a mi panel" : "Ir a iniciar sesión"}</Link>
            </div>
          )}

          {state.kind === "offline" && (
            <div className="stack">
              <Alert kind="error" title="No pudimos revisar la invitación">
                {state.message}
              </Alert>
              <Button variant="secondary" icon="refresh" onClick={() => setAttempt((n) => n + 1)}>
                Reintentar
              </Button>
            </div>
          )}

          {state.kind === "ready" && (
            <>
              <div>
                <h1>Te invitaron a {state.preview.tenant_name}</h1>
                <p className="login-lead">
                  Entrarás como <strong>{roleLabel(state.preview.role)}</strong> con el correo{" "}
                  <strong>{state.preview.email}</strong>.
                  {state.preview.has_account
                    ? " Ya tienes cuenta en Saint: escribe tu contraseña de siempre."
                    : " Crea tu cuenta para empezar."}
                </p>
              </div>

              <form className="login-form" onSubmit={(e) => void submit(e, state.preview)} noValidate>
                {problem && (
                  <Alert kind={problem.kind} title={problem.title}>
                    {problem.text}
                  </Alert>
                )}
                {session && session.user.email.toLowerCase() !== state.preview.email.toLowerCase() && (
                  <Alert kind="info" title="Cambiarás de cuenta">
                    Ahora estás dentro como {session.user.email}. Al aceptar entrarás como {state.preview.email}.
                  </Alert>
                )}

                {!state.preview.has_account && (
                  <TextField
                    label="Tu nombre"
                    autoComplete="name"
                    value={fullName}
                    maxLength={LIMITS.personName}
                    onChange={(e) => setFullName(e.target.value)}
                    error={errors.name}
                    data-autofocus
                  />
                )}

                <div className="field">
                  <label htmlFor="clave-invitacion">{state.preview.has_account ? "Tu contraseña" : "Contraseña nueva"}</label>
                  <div className="password">
                    <input
                      id="clave-invitacion"
                      className="input"
                      type={showPassword ? "text" : "password"}
                      autoComplete={state.preview.has_account ? "current-password" : "new-password"}
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      aria-invalid={errors.password ? "true" : undefined}
                      aria-describedby="clave-invitacion-nota"
                    />
                    <button
                      type="button"
                      className="btn-icon"
                      aria-label={showPassword ? "Ocultar contraseña" : "Mostrar contraseña"}
                      onClick={() => setShowPassword((v) => !v)}
                    >
                      <Icon name={showPassword ? "eyeOff" : "eye"} />
                    </button>
                  </div>
                  <span id="clave-invitacion-nota" className={errors.password ? "field-error" : "field-hint"}>
                    {errors.password ?? (state.preview.has_account ? "" : `Mínimo ${LIMITS.password} caracteres.`)}
                  </span>
                </div>

                {!state.preview.has_account && (
                  <TextField
                    label="Repite la contraseña"
                    type={showPassword ? "text" : "password"}
                    autoComplete="new-password"
                    value={repeat}
                    onChange={(e) => setRepeat(e.target.value)}
                    error={errors.repeat}
                  />
                )}

                <label className="check" htmlFor="recordar-invitacion">
                  <input
                    id="recordar-invitacion"
                    type="checkbox"
                    checked={remember}
                    onChange={(e) => setRemember(e.target.checked)}
                  />
                  Mantener la sesión iniciada
                </label>

                <Button type="submit" className="btn-big" busy={busy}>
                  {busy ? "Entrando…" : state.preview.has_account ? "Aceptar y entrar" : "Crear mi cuenta y entrar"}
                </Button>
                <p className="login-help">La invitación vence el {dateTime(state.preview.expires_at)}.</p>
              </form>
            </>
          )}
        </div>
        <p className="login-foot">© 2026 Saint Agente IA</p>
      </main>

      <aside className="login-aside" aria-hidden="true">
        <div className="hero">
          <span className="hero-ring" style={{ right: -140, top: -140, width: 420, height: 420 }} />
          <span className="hero-ring" style={{ right: -60, top: -60, width: 260, height: 260 }} />
          <div className="hero-text">
            <span className="pill">
              <span className="dot" />
              Equipo
            </span>
            <h2>Atiendan juntos a sus clientes.</h2>
            <p>El agente de IA contesta al instante y cualquiera del equipo puede tomar la conversación cuando haga falta.</p>
          </div>
        </div>
      </aside>
    </div>
  );
}
