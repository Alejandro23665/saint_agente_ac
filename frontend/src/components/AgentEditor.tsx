import { useEffect, useState } from "react";

import { agentApi } from "../api/agent";
import type { Agent } from "../api/types";
import { LIMITS } from "../api/types";
import { timeAgo } from "../lib/format";
import { errorText } from "../lib/useResource";
import { Alert } from "./Alert";
import { Button } from "./Button";
import { StatusPill } from "./Feedback";
import { TextArea, TextField } from "./Field";
import { useToast } from "./Toast";

/**
 * Nombre e instrucciones del agente. Cada cambio de instrucciones crea una versión nueva y el
 * agente la usa desde su siguiente respuesta. Se puede traer el texto de fábrica al cuadro
 * ("Restablecer") sin guardarlo todavía.
 */
export function AgentEditor({ agent, onSaved }: { agent: Agent; onSaved: (agent: Agent) => void }) {
  const toast = useToast();
  const readOnly = !agent.can_edit;
  const [name, setName] = useState(agent.name);
  const [instructions, setInstructions] = useState(agent.instructions);
  const [busy, setBusy] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const nameChanged = name.trim() !== agent.name;
  const instructionsChanged = instructions.trim() !== agent.instructions.trim();
  const dirty = nameChanged || instructionsChanged;

  const length = instructions.trim().length;
  const instructionsProblem =
    length < LIMITS.instructionsMin ? `Escribe al menos ${LIMITS.instructionsMin} caracteres para que el agente sepa qué hacer.` : null;
  const nameProblem = !name.trim() ? "El agente necesita un nombre." : null;
  const invalid = Boolean(instructionsProblem || nameProblem);

  // Si cierras la pestaña con cambios sin guardar, el navegador pregunta.
  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault();
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  async function save() {
    setBusy(true);
    setError(null);
    try {
      const saved = await agentApi.update({
        ...(nameChanged ? { name: name.trim() } : {}),
        ...(instructionsChanged ? { instructions: instructions.trim() } : {}),
      });
      toast.success(instructionsChanged ? `Guardado como versión ${saved.version}` : "Guardado");
      onSaved(saved);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  async function reset() {
    setResetting(true);
    setError(null);
    try {
      const factory = await agentApi.defaultInstructions();
      setInstructions(factory.instructions);
      toast.show("Se cargó el texto de fábrica. Revísalo y pulsa Guardar.");
    } catch (e) {
      setError(errorText(e));
    } finally {
      setResetting(false);
    }
  }

  function discard() {
    setName(agent.name);
    setInstructions(agent.instructions);
    setError(null);
  }

  return (
    <section className="card" aria-labelledby="agente-titulo">
      <div className="card-head">
        <h2 id="agente-titulo">Instrucciones del agente</h2>
        {dirty ? <StatusPill tone="warn">Cambios sin guardar</StatusPill> : <StatusPill tone="muted">Versión {agent.version}</StatusPill>}
      </div>
      <p className="muted">
        Es lo que lee el agente antes de contestar: cómo se llama, cómo habla y qué debe hacer. Usa las instrucciones nuevas desde su
        siguiente respuesta. Modelo: {agent.model}. Última actualización: {timeAgo(agent.updated_at)}.
      </p>

      {readOnly && (
        <Alert kind="info" title="Solo lectura">
          Las instrucciones las cambia el dueño o un administrador.
        </Alert>
      )}
      {error && (
        <Alert kind="error" title="No se pudo hacer">
          {error}
        </Alert>
      )}

      <TextField
        label="Nombre del agente"
        value={name}
        maxLength={LIMITS.agentName}
        readOnly={readOnly}
        onChange={(e) => setName(e.target.value)}
        error={dirty ? nameProblem : null}
      />
      <TextArea
        label="Instrucciones"
        rows={14}
        maxLength={LIMITS.instructions}
        counter
        value={instructions}
        readOnly={readOnly}
        onChange={(e) => setInstructions(e.target.value)}
        error={dirty ? instructionsProblem : null}
      />

      {!readOnly && (
        <div className="row row-end">
          <Button variant="ghost" onClick={() => void reset()} busy={resetting} disabled={busy}>
            Restablecer las de fábrica
          </Button>
          <Button variant="secondary" onClick={discard} disabled={!dirty || busy}>
            Descartar cambios
          </Button>
          <Button onClick={() => void save()} busy={busy} disabled={!dirty || invalid}>
            Guardar
          </Button>
        </div>
      )}
    </section>
  );
}
