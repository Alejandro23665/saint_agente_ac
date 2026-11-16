import { Link } from "react-router-dom";

import type { NumberStatus } from "../api/types";
import { useAuth } from "../auth/session";
import { Alert } from "../components/Alert";
import { Icon } from "../components/Icon";
import { useDashboard, useInboxCounts } from "../layout/dashboard";

const NUMBER_STATUS: Record<NumberStatus, { label: string; pill: string }> = {
  open: { label: "Conectado", pill: "pill-success" },
  connecting: { label: "Conectando", pill: "pill-warn" },
  qr_pending: { label: "Falta escanear el QR", pill: "pill-warn" },
  created: { label: "Falta escanear el QR", pill: "pill-warn" },
  close: { label: "Desconectado", pill: "pill-danger" },
  banned: { label: "Bloqueado por WhatsApp", pill: "pill-danger" },
};

function Count({ value, capped }: { value: number | undefined; capped?: boolean }) {
  if (value === undefined) return <span className="skeleton" aria-label="Cargando" />;
  return <strong>{capped ? "100+" : value}</strong>;
}

function Stat({ label, value, capped, highlight }: { label: string; value?: number; capped?: boolean; highlight?: boolean }) {
  return (
    <Link to="/bandeja" className={highlight ? "stat stat-highlight" : "stat"}>
      <span>{label}</span>
      <Count value={value} capped={capped} />
    </Link>
  );
}

interface Step {
  text: string;
  done: boolean;
  action: string;
  to: string;
}

export function Home() {
  const { session } = useAuth();
  const data = useDashboard();
  const counts = useInboxCounts(session?.user.id ?? "");
  if (!session?.active_tenant) return null;
  const tenant = session.active_tenant;
  const firstName = session.user.full_name.split(" ")[0];

  const steps: Step[] = [
    {
      text: "Conecta tu número de WhatsApp",
      done: data.numbers?.some((n) => n.status === "open") ?? false,
      action: "Conectar",
      to: "/numeros",
    },
    {
      text: "Revisa lo que dice tu agente",
      done: (data.agent?.version ?? 1) > 1,
      action: "Revisar",
      to: "/agente",
    },
    {
      text: "Sube lo que el bot debe saber: horarios, productos, precios",
      done: data.knowledge?.some((k) => k.status === "ready") ?? false,
      action: "Subir",
      to: "/conocimiento",
    },
    {
      text: "Invita a tu equipo para atender juntos",
      done: (data.members?.length ?? 0) > 1,
      action: "Invitar",
      to: "/equipo",
    },
  ];
  const doneCount = steps.filter((s) => s.done).length;
  const number = data.numbers?.[0];
  const status = number ? NUMBER_STATUS[number.status] : null;

  return (
    <main className="page">
      <div className="page-head">
        <h1>Hola, {firstName}</h1>
        <p>Así va {tenant.name} hoy. Los números se actualizan solos.</p>
      </div>

      {data.failed && (
        <Alert kind="warn" title="No pudimos cargar todo">
          Algunos datos no llegaron. Se volverán a pedir solos en cuanto haya conexión.
        </Alert>
      )}

      <section className="stats" aria-label="Resumen de la bandeja">
        <Stat label="Conversaciones abiertas" value={counts?.open} capped={counts?.capped} />
        <Stat label="Esperan respuesta" value={counts?.waiting} highlight />
        <Stat label="Las atiende una persona" value={counts?.human} />
        <Stat label="Asignadas a mí" value={counts?.mine} />
      </section>

      <div className="home-grid">
        <section className="card steps-card">
          <div className="card-head">
            <h2>Primeros pasos</h2>
            <small>
              {doneCount} de {steps.length} listos
            </small>
          </div>
          <ol className="steps">
            {steps.map((step, index) => (
              <li key={step.to} className={step.done ? "step step-done" : "step"}>
                <span className="step-mark">{step.done ? <Icon name="check" size={16} /> : index + 1}</span>
                <span>{step.text}</span>
                {step.done ? <span className="step-ok">Listo</span> : <Link to={step.to}>{step.action}</Link>}
              </li>
            ))}
          </ol>
        </section>

        <section className="card">
          <h2>{data.numbers && data.numbers.length > 1 ? "Tus números" : "Tu número"}</h2>
          {data.numbers === null ? (
            <span className="skeleton" style={{ width: "100%", height: 44 }} aria-label="Cargando" />
          ) : number && status ? (
            <>
              <div className="number-row">
                <span className="icon-box">
                  <Icon name="phone" />
                </span>
                <span>
                  <strong>{number.phone ?? "Número sin vincular"}</strong>
                  <small>
                    {data.numbers.length > 1 ? `y ${data.numbers.length - 1} más` : "Tu número de WhatsApp"}
                  </small>
                </span>
              </div>
              <span className={`pill ${status.pill}`} style={{ alignSelf: "flex-start" }}>
                {status.label}
              </span>
              <p className="muted">
                Si el teléfono se desconecta, aquí te avisamos al momento y te mostramos cómo volver a conectarlo.
              </p>
            </>
          ) : (
            <p className="muted">Todavía no conectas un número. Conéctalo con un QR para que tu agente empiece a responder.</p>
          )}
          <Link to="/numeros">{number ? "Ver números" : "Conectar un número"}</Link>
        </section>
      </div>
    </main>
  );
}
