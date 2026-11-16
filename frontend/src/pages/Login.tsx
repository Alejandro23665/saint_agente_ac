import { useState } from "react";
import type { FormEvent } from "react";

import { ApiError } from "../api/client";
import { useAuth } from "../auth/session";
import { Alert } from "../components/Alert";
import { Icon } from "../components/Icon";
import { Logo } from "../components/Logo";

type Problem = { kind: "error" | "warn"; title: string; text: string } | null;

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

function problemFor(error: unknown): Problem {
  if (error instanceof ApiError && error.status === 401) {
    return {
      kind: "error",
      title: "No pudimos iniciar sesión",
      text: "El correo o la contraseña no coinciden. Revísalos e inténtalo otra vez.",
    };
  }
  if (error instanceof ApiError && error.status === 429) {
    return {
      kind: "warn",
      title: "Espera unos minutos",
      text: "Hubo muchos intentos seguidos. Por seguridad, vuelve a probar en 15 minutos.",
    };
  }
  return {
    kind: "error",
    title: "No pudimos iniciar sesión",
    text: error instanceof ApiError ? error.message : "Algo salió mal. Vuelve a intentar.",
  };
}

export function Login() {
  const { login, notice } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [remember, setRemember] = useState(true);
  const [showPassword, setShowPassword] = useState(false);
  const [emailError, setEmailError] = useState<string | null>(null);
  const [problem, setProblem] = useState<Problem>(null);
  const [busy, setBusy] = useState(false);
  const [showForgot, setShowForgot] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    const cleanEmail = email.trim();
    if (!EMAIL.test(cleanEmail)) {
      setEmailError("Escribe un correo completo, por ejemplo nombre@negocio.com");
      return;
    }
    setEmailError(null);
    setProblem(null);
    setBusy(true);
    try {
      await login(cleanEmail, password, remember);
    } catch (error) {
      setProblem(problemFor(error));
      setBusy(false);
    }
  }

  return (
    <div className="login">
      <main className="login-main">
        <Logo width={150} />
        <div className="login-body">
          <div>
            <h1>Inicia sesión en tu panel</h1>
            <p className="login-lead">
              Supervisa las conversaciones de tu negocio y lo que responde tu agente de IA.
            </p>
          </div>

          <form className="login-form" onSubmit={submit} noValidate>
            {problem && (
              <Alert kind={problem.kind} title={problem.title}>
                {problem.text}
              </Alert>
            )}
            {!problem && notice === "expired" && (
              <Alert kind="info" title="Vuelve a iniciar sesión">
                Por seguridad cerramos tu sesión. Tus conversaciones siguen igual.
              </Alert>
            )}

            <div className="field">
              <label htmlFor="correo">Correo</label>
              <input
                id="correo"
                className="input"
                type="email"
                autoComplete="email"
                placeholder="tu@negocio.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                aria-invalid={emailError ? "true" : undefined}
                aria-describedby={emailError ? "correo-error" : undefined}
                required
              />
              {emailError && (
                <span id="correo-error" className="field-error">
                  {emailError}
                </span>
              )}
            </div>

            <div className="field">
              <label htmlFor="clave">Contraseña</label>
              <div className="password">
                <input
                  id="clave"
                  className="input"
                  type={showPassword ? "text" : "password"}
                  autoComplete="current-password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
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
            </div>

            <div className="login-row">
              <label className="check" htmlFor="recordar">
                <input
                  id="recordar"
                  type="checkbox"
                  checked={remember}
                  onChange={(e) => setRemember(e.target.checked)}
                />
                Mantener la sesión iniciada
              </label>
              <button type="button" className="link-button" onClick={() => setShowForgot((v) => !v)}>
                ¿Olvidaste tu contraseña?
              </button>
            </div>
            {showForgot && (
              <p className="login-help" role="status">
                Por ahora, pídele al dueño de tu negocio que te envíe una invitación nueva desde
                Equipo. Pronto podrás cambiarla tú mismo.
              </p>
            )}

            <button type="submit" className="btn btn-primary btn-big" disabled={busy} aria-busy={busy}>
              {busy && <Icon name="spinner" size={18} className="spin" />}
              {busy ? "Entrando…" : "Iniciar sesión"}
            </button>
          </form>

          <p className="login-help">
            ¿Tu negocio aún no usa Saint?{" "}
            <a href="https://landing-page-know.vercel.app/" target="_blank" rel="noreferrer">
              Escríbenos
            </a>
          </p>
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
              Agente IA activo
            </span>
            <h2>Tu agente responde. Tú decides cuándo intervenir.</h2>
            <p>Revisa cada conversación de WhatsApp en un solo lugar y toma el control cuando un cliente lo necesite.</p>
          </div>
          <div className="hero-chat">
            <div className="hero-chat-head">
              <span className="avatar">MP</span>
              <span>
                <strong>María Pérez</strong>
                Panadería · WhatsApp
              </span>
              <span className="pill pill-ice">Agente IA</span>
            </div>
            <div className="bubble bubble-in">Hola, ¿a qué hora abren el domingo?</div>
            <div className="bubble bubble-out">¡Hola María! El domingo abrimos de 8:00 a 13:00. ¿Te aparto algo?</div>
            <div className="bubble bubble-in">Sí, dos panes dulces por favor</div>
          </div>
        </div>
      </aside>
    </div>
  );
}
