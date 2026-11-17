import { useState } from "react";

import { refreshSession } from "../api/client";
import { teamApi } from "../api/team";
import type { TenantDetail, TenantUpdateIn } from "../api/types";
import { LIMITS } from "../api/types";
import { useDashboardRefresh } from "../layout/dashboard";
import { countryOptions, zoneOptions } from "../lib/regions";
import { errorText } from "../lib/useResource";
import { Alert } from "./Alert";
import { Button } from "./Button";
import { SelectField, TextField } from "./Field";
import { useToast } from "./Toast";

/**
 * Nombre, país y zona horaria del negocio. La zona horaria es la que se usa para el horario
 * de atención. El identificador (slug) no cambia: lo usan los scripts.
 */
export function BusinessSettings({ tenant, onSaved }: { tenant: TenantDetail; onSaved: (tenant: TenantDetail) => void }) {
  const toast = useToast();
  const refreshDashboard = useDashboardRefresh();
  const readOnly = !tenant.can_edit;
  const [name, setName] = useState(tenant.name);
  const [country, setCountry] = useState(tenant.country_code);
  const [zone, setZone] = useState(tenant.timezone);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const changes: TenantUpdateIn = {};
  if (name.trim() !== tenant.name) changes.name = name.trim();
  if (country !== tenant.country_code) changes.country_code = country;
  if (zone !== tenant.timezone) changes.timezone = zone;
  const dirty = Object.keys(changes).length > 0;
  const nameProblem = !name.trim() ? "El negocio necesita un nombre." : null;

  const zones = zoneOptions(tenant.timezone);

  async function save() {
    setBusy(true);
    setError(null);
    try {
      const saved = await teamApi.updateTenant(changes);
      toast.success("Ajustes guardados");
      onSaved(saved);
      // El nombre se ve en el menú y la zona horaria cambia si el negocio está abierto ahora.
      refreshDashboard(["hours"]);
      if (changes.name) await refreshSession(tenant.id).catch(() => null);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card" aria-labelledby="negocio-titulo">
      <h2 id="negocio-titulo">Negocio</h2>
      {readOnly && (
        <Alert kind="info" title="Solo lectura">
          Los datos del negocio los cambia el dueño o un administrador.
        </Alert>
      )}
      {error && (
        <Alert kind="error" title="No se pudo guardar">
          {error}
        </Alert>
      )}

      <TextField
        label="Nombre del negocio"
        value={name}
        maxLength={LIMITS.tenantName}
        readOnly={readOnly}
        onChange={(e) => setName(e.target.value)}
        error={dirty ? nameProblem : null}
      />
      <div className="grid-2">
        <SelectField label="País" value={country} disabled={readOnly} onChange={(e) => setCountry(e.target.value)}>
          {countryOptions(tenant.country_code).map((c) => (
            <option key={c.code} value={c.code}>
              {c.name}
            </option>
          ))}
        </SelectField>
        <SelectField
          label="Zona horaria"
          value={zone}
          disabled={readOnly}
          onChange={(e) => setZone(e.target.value)}
          hint="Con esta zona se calcula tu horario de atención."
        >
          <optgroup label="Más usadas">
            {zones.common.map((z) => (
              <option key={z} value={z}>
                {z}
              </option>
            ))}
          </optgroup>
          <optgroup label="Todas">
            {zones.rest.map((z) => (
              <option key={z} value={z}>
                {z}
              </option>
            ))}
          </optgroup>
        </SelectField>
      </div>
      <p className="muted">
        Identificador del negocio: <strong>{tenant.slug}</strong> (no cambia).
      </p>

      {!readOnly && (
        <div className="row row-end">
          <Button
            variant="secondary"
            disabled={!dirty || busy}
            onClick={() => {
              setName(tenant.name);
              setCountry(tenant.country_code);
              setZone(tenant.timezone);
              setError(null);
            }}
          >
            Descartar cambios
          </Button>
          <Button onClick={() => void save()} busy={busy} disabled={!dirty || Boolean(nameProblem)}>
            Guardar
          </Button>
        </div>
      )}
    </section>
  );
}
