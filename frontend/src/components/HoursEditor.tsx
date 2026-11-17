import { useMemo, useState } from "react";

import type { DayKey, Hours, HoursIn } from "../api/types";
import { LIMITS } from "../api/types";
import { allClosed, copyDay, DAYS, DEFAULT_RANGE, fromDraft, MAX_RANGES, normalizeMessage, toDraft, validateDraft, weekdayDraft } from "../lib/hours";
import type { Draft, TimeRange } from "../lib/hours";
import { errorText } from "../lib/useResource";
import { Alert } from "./Alert";
import { Button } from "./Button";
import { StatusPill } from "./Feedback";
import { Switch, TextArea } from "./Field";
import { Icon } from "./Icon";
import { useToast } from "./Toast";

/** Lo que se guardaría con este borrador: sirve para saber si hay cambios sin guardar. */
function payload(enabled: boolean, draft: Draft, message: string): HoursIn {
  return {
    days: enabled ? fromDraft(draft) : {},
    after_hours_message: normalizeMessage(message) || null,
  };
}

interface Props {
  hours: Hours;
  onSaved: (hours: Hours) => void;
  save: (value: HoursIn) => Promise<Hours>;
}

/**
 * Horario de atención. Apagado: el negocio cuenta como abierto siempre. Encendido: cada día
 * tiene hasta 3 tramos; un día sin tramos está cerrado. Las horas son del huso del negocio.
 */
export function HoursEditor({ hours, onSaved, save }: Props) {
  const toast = useToast();
  const readOnly = !hours.can_edit;
  const serverEnabled = Object.keys(hours.days).length > 0;
  const [enabled, setEnabled] = useState(serverEnabled);
  const [draft, setDraft] = useState<Draft>(() => toDraft(hours.days));
  const [message, setMessage] = useState(hours.after_hours_message ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const errors = useMemo(() => (enabled ? validateDraft(draft) : {}), [enabled, draft]);
  const invalid = Object.keys(errors).length > 0 || message.length > LIMITS.afterHoursMessage;
  const dirty = useMemo(
    () => JSON.stringify(payload(enabled, draft, message)) !== JSON.stringify(payload(serverEnabled, toDraft(hours.days), hours.after_hours_message ?? "")),
    [enabled, draft, message, serverEnabled, hours],
  );

  function turn(on: boolean) {
    setEnabled(on);
    // Al encender por primera vez, de lunes a viernes de 8:00 a 18:00 para no empezar de cero.
    if (on && allClosed(draft)) setDraft(weekdayDraft());
  }

  function change(day: DayKey, ranges: TimeRange[]) {
    setDraft((current) => ({ ...current, [day]: ranges }));
  }

  function discard() {
    setEnabled(serverEnabled);
    setDraft(toDraft(hours.days));
    setMessage(hours.after_hours_message ?? "");
    setError(null);
  }

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      const saved = await save(payload(enabled, draft, message));
      toast.success("Horario guardado");
      onSaved(saved);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card" aria-labelledby="horario-titulo">
      <div className="card-head">
        <h2 id="horario-titulo">Horario de atención</h2>
        {enabled && !dirty && (
          <StatusPill tone={hours.open_now ? "success" : "warn"} dot>
            {hours.open_now ? "Abierto ahora" : "Fuera de horario"}
          </StatusPill>
        )}
      </div>
      <p className="muted">
        Las horas son de la zona horaria del negocio: <strong>{hours.timezone}</strong>. Se cambia en Ajustes.
      </p>

      {readOnly && (
        <Alert kind="info" title="Solo lectura">
          El horario lo cambia el dueño o un administrador.
        </Alert>
      )}
      {error && (
        <Alert kind="error" title="No se pudo guardar el horario">
          {error}
        </Alert>
      )}

      <Switch
        label="Tengo un horario de atención"
        hint="Si lo apagas, el negocio cuenta como abierto siempre."
        checked={enabled}
        onChange={turn}
        disabled={readOnly}
      />

      {enabled && (
        <>
          {allClosed(draft) && (
            <Alert kind="warn" title="Todos los días están cerrados">
              Guardado así, el negocio figurará siempre fuera de horario. Abre al menos un día.
            </Alert>
          )}
          <ul className="week">
            {DAYS.map(({ key, label }) => {
              const ranges = draft[key];
              const open = ranges.length > 0;
              return (
                <li key={key} className="day-row">
                  <label className="day-name check" htmlFor={`dia-${key}`}>
                    <input
                      id={`dia-${key}`}
                      type="checkbox"
                      role="switch"
                      aria-label={`${label} abierto`}
                      checked={open}
                      disabled={readOnly}
                      onChange={(e) => change(key, e.target.checked ? [{ ...DEFAULT_RANGE }] : [])}
                    />
                    <strong>{label}</strong>
                  </label>

                  <div className="day-ranges">
                    {!open && <span className="muted">Cerrado</span>}
                    {ranges.map((range, index) => (
                      <div key={index} className="range">
                        <input
                          type="time"
                          className="input input-time"
                          aria-label={`${label}, tramo ${index + 1}, abre`}
                          value={range.start}
                          disabled={readOnly}
                          onChange={(e) => change(key, ranges.map((r, i) => (i === index ? { ...r, start: e.target.value } : r)))}
                        />
                        <span aria-hidden="true">a</span>
                        <input
                          type="time"
                          className="input input-time"
                          aria-label={`${label}, tramo ${index + 1}, cierra`}
                          value={range.end}
                          disabled={readOnly}
                          onChange={(e) => change(key, ranges.map((r, i) => (i === index ? { ...r, end: e.target.value } : r)))}
                        />
                        {!readOnly && (
                          <button
                            type="button"
                            className="btn-icon"
                            aria-label={`Quitar el tramo ${index + 1} del ${label.toLowerCase()}`}
                            onClick={() => change(key, ranges.filter((_, i) => i !== index))}
                          >
                            <Icon name="close" size={18} />
                          </button>
                        )}
                      </div>
                    ))}
                    {errors[key] && (
                      <span className="field-error" role="alert">
                        {errors[key]}
                      </span>
                    )}
                    {!readOnly && open && (
                      <span className="day-tools">
                        {ranges.length < MAX_RANGES && (
                          <button
                            type="button"
                            className="link-button"
                            onClick={() => change(key, [...ranges, { start: ranges[ranges.length - 1].end, end: "23:00" }])}
                          >
                            Agregar tramo
                          </button>
                        )}
                        <button type="button" className="link-button" onClick={() => setDraft((d) => copyDay(d, key))}>
                          Copiar a todos los días
                        </button>
                      </span>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        </>
      )}

      <TextArea
        label="Mensaje fuera de horario"
        hint="Es lo que se le avisará al cliente cuando escriba con el negocio cerrado."
        placeholder="Ya cerramos. Mañana a las 8:00 te respondemos."
        rows={3}
        maxLength={LIMITS.afterHoursMessage}
        counter
        value={message}
        readOnly={readOnly}
        onChange={(e) => setMessage(e.target.value)}
      />

      <Alert kind="info" title="El agente todavía no usa el horario">
        Por ahora el horario y el mensaje se guardan y se muestran en el panel. Que el agente conteste distinto fuera de horario
        llega con el bloque 4.6.
      </Alert>

      {!readOnly && (
        <div className="row row-end">
          <Button variant="secondary" onClick={discard} disabled={!dirty || busy}>
            Descartar cambios
          </Button>
          <Button onClick={() => void submit()} busy={busy} disabled={!dirty || invalid}>
            Guardar horario
          </Button>
        </div>
      )}
    </section>
  );
}
