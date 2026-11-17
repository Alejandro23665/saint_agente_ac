import { agentApi } from "../api/agent";
import { Alert } from "../components/Alert";
import { AgentEditor } from "../components/AgentEditor";
import { Spinner } from "../components/Feedback";
import { HoursEditor } from "../components/HoursEditor";
import { VersionHistory } from "../components/VersionHistory";
import { useDashboardRefresh } from "../layout/dashboard";
import { useResource } from "../lib/useResource";

export function AgentPage() {
  const refreshDashboard = useDashboardRefresh();
  const agent = useResource(agentApi.get);
  const versions = useResource(agentApi.versions);
  const hours = useResource(agentApi.hours);

  return (
    <main className="page">
      <div className="page-head">
        <h1>Agente y horario</h1>
        <p>Lo que dice tu agente de IA y cuándo atiende tu negocio.</p>
      </div>

      {agent.error && !agent.data && (
        <Alert kind="error" title="No pudimos cargar el agente">
          {agent.error}{" "}
          <button type="button" className="link-button" onClick={() => void agent.reload()}>
            Reintentar
          </button>
        </Alert>
      )}
      {agent.loading ? (
        <div className="center-box">
          <Spinner label="Cargando el agente" size={28} />
        </div>
      ) : (
        agent.data && (
          // Al guardar o volver a una versión, el editor se rehace con lo nuevo.
          <AgentEditor
            key={`${agent.data.version}-${agent.data.updated_at}`}
            agent={agent.data}
            onSaved={(saved) => {
              agent.setData(saved);
              void versions.reload();
              refreshDashboard(["agent"]);
            }}
          />
        )
      )}

      {agent.data && (
        <VersionHistory
          versions={versions}
          canEdit={agent.data.can_edit}
          onRestored={(restored) => {
            agent.setData(restored);
            void versions.reload();
            refreshDashboard(["agent"]);
          }}
        />
      )}

      {hours.error && !hours.data && (
        <Alert kind="error" title="No pudimos cargar el horario">
          {hours.error}{" "}
          <button type="button" className="link-button" onClick={() => void hours.reload()}>
            Reintentar
          </button>
        </Alert>
      )}
      {hours.data && (
        <HoursEditor
          key={JSON.stringify([hours.data.days, hours.data.after_hours_message])}
          hours={hours.data}
          save={agentApi.setHours}
          onSaved={(saved) => {
            hours.setData(saved);
            refreshDashboard(["hours"]);
          }}
        />
      )}
    </main>
  );
}
