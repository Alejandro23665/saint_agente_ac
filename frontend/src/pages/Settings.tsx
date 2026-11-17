import { teamApi } from "../api/team";
import { AccountSettings } from "../components/AccountSettings";
import { Alert } from "../components/Alert";
import { BusinessSettings } from "../components/BusinessSettings";
import { Spinner } from "../components/Feedback";
import { useResource } from "../lib/useResource";

export function Settings() {
  const tenant = useResource(teamApi.tenant);

  return (
    <main className="page">
      <div className="page-head">
        <h1>Ajustes</h1>
        <p>Los datos de tu negocio y de tu cuenta.</p>
      </div>

      {tenant.error && !tenant.data && (
        <Alert kind="error" title="No pudimos cargar los datos del negocio">
          {tenant.error}{" "}
          <button type="button" className="link-button" onClick={() => void tenant.reload()}>
            Reintentar
          </button>
        </Alert>
      )}
      {tenant.loading ? (
        <div className="center-box">
          <Spinner label="Cargando los ajustes" size={28} />
        </div>
      ) : (
        tenant.data && (
          // Al guardar, el formulario se rehace con lo que devolvió la API.
          <BusinessSettings
            key={`${tenant.data.name}|${tenant.data.country_code}|${tenant.data.timezone}`}
            tenant={tenant.data}
            onSaved={tenant.setData}
          />
        )
      )}

      <AccountSettings />
    </main>
  );
}
