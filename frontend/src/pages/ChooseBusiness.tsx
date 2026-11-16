import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { ApiError } from "../api/client";
import { initials, ROLE_NAMES, useAuth } from "../auth/session";
import { Alert } from "../components/Alert";
import { Icon } from "../components/Icon";
import { Logo } from "../components/Logo";

export function ChooseBusiness() {
  const { session, selectTenant, logout } = useAuth();
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();
  if (!session) return null;
  const tenants = session.tenants;

  async function choose(id: string) {
    setBusy(id);
    setError(null);
    try {
      await selectTenant(id);
      navigate("/", { replace: true });
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "No pudimos abrir ese negocio. Vuelve a intentar.");
      setBusy(null);
    }
  }

  return (
    <div className="choose">
      <header className="choose-head">
        <Logo width={130} />
        <div>
          <span>{session.user.email}</span>
          <button type="button" className="btn btn-secondary" onClick={() => void logout()}>
            Cerrar sesión
          </button>
        </div>
      </header>

      <main className="choose-main">
        {tenants.length === 0 ? (
          <header>
            <h1>Todavía no tienes un negocio</h1>
            <p>Pídele al dueño del negocio que te invite desde Equipo. Cuando aceptes, aparecerá aquí.</p>
          </header>
        ) : (
          <>
            <header>
              <h1>¿Qué negocio quieres atender?</h1>
              <p>
                Tienes acceso a {tenants.length} negocios. Puedes cambiar en cualquier momento desde el menú.
              </p>
            </header>
            {error && (
              <Alert kind="error" title="No se pudo abrir">
                {error}
              </Alert>
            )}
            <ul className="business-list">
              {tenants.map((tenant) => (
                <li key={tenant.id}>
                  <button
                    type="button"
                    className="business"
                    onClick={() => void choose(tenant.id)}
                    disabled={busy !== null}
                    aria-busy={busy === tenant.id}
                  >
                    <span className="avatar">{initials(tenant.name)}</span>
                    <span>
                      <strong>{tenant.name}</strong>
                      <small>{ROLE_NAMES[tenant.role] ?? tenant.role}</small>
                    </span>
                    <Icon name={busy === tenant.id ? "spinner" : "chevronRight"} className={busy === tenant.id ? "spin" : undefined} />
                  </button>
                </li>
              ))}
            </ul>
          </>
        )}
      </main>
    </div>
  );
}
