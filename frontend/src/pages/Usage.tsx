import { useState } from "react";

import { usageApi } from "../api/usage";
import type { UsagePeriod, UsageReport } from "../api/types";
import { useRole } from "../auth/session";
import { Alert } from "../components/Alert";
import { Button } from "../components/Button";
import { EmptyState, Spinner } from "../components/Feedback";
import { UsageChart } from "../components/UsageChart";
import { useEvents } from "../layout/dashboard";
import { count } from "../lib/format";
import { compact, dayLabel, isEmpty, METRICS, money, PERIODS, periodLine, totalTokens, trackingNotice } from "../lib/usage";
import type { MetricKey } from "../lib/usage";
import { useResource } from "../lib/useResource";

function Card({ label, value, note, highlight }: { label: string; value: string; note?: string; highlight?: boolean }) {
  return (
    <div className={highlight ? "usage-card usage-card-highlight" : "usage-card"}>
      <span>{label}</span>
      <strong>{value}</strong>
      {note && <small>{note}</small>}
    </div>
  );
}

function Report({ report, metric }: { report: UsageReport; metric: MetricKey }) {
  const t = report.totals;
  const notice = trackingNotice(report);
  const priced = report.pricing.configured && report.estimated_cost_usd !== null;

  return (
    <>
      <p className="muted">
        {periodLine(report.start, report.end)} · zona horaria {report.timezone}
      </p>
      {notice && (
        <Alert kind="info" title={report.tracking_since === null ? "Sin datos todavía" : "Datos parciales"}>
          {notice}
        </Alert>
      )}

      <section className="usage-cards" aria-label="Resumen del periodo">
        <Card label="Conversaciones nuevas" value={count(t.conversations)} />
        <Card label="Mensajes recibidos" value={count(t.inbound_messages)} />
        <Card label="Mensajes enviados" value={count(t.outbound_messages)} />
        <Card label="Respuestas de IA" value={count(t.ai_replies)} />
        <Card
          label="Tokens de IA"
          value={compact(totalTokens(report))}
          note={`${count(t.input_tokens)} de entrada · ${count(t.output_tokens)} de salida`}
        />
        <Card
          label="Costo estimado de la IA"
          value={priced ? money(report.estimated_cost_usd as number) : "Sin precios"}
          note={priced ? "Según los precios del .env" : "Pon los precios en el .env para estimarlo"}
          highlight={priced}
        />
      </section>

      {!isEmpty(report) && (
        <>
          <section className="card" aria-labelledby="grafico-titulo">
            <h2 id="grafico-titulo">{(METRICS.find((m) => m.key === metric) ?? METRICS[0]).label} por día</h2>
            <UsageChart days={report.days} metric={metric} />
          </section>

          <details className="card usage-table">
            <summary>Ver los números de cada día</summary>
            <div className="table-wrap">
              <table className="matrix">
                <thead>
                  <tr>
                    <th scope="col">Día</th>
                    <th scope="col">Conversaciones</th>
                    <th scope="col">Recibidos</th>
                    <th scope="col">Enviados</th>
                    <th scope="col">Respuestas de IA</th>
                    <th scope="col">Tokens de entrada</th>
                    <th scope="col">Tokens de salida</th>
                  </tr>
                </thead>
                <tbody>
                  {report.days.map((d) => (
                    <tr key={d.day}>
                      <th scope="row">{dayLabel(d.day)}</th>
                      <td>{count(d.conversations)}</td>
                      <td>{count(d.inbound_messages)}</td>
                      <td>{count(d.outbound_messages)}</td>
                      <td>{count(d.ai_replies)}</td>
                      <td>{count(d.input_tokens)}</td>
                      <td>{count(d.output_tokens)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
        </>
      )}
    </>
  );
}

export function Usage() {
  const { canEdit } = useRole();
  const [period, setPeriod] = useState<UsagePeriod>("this_month");
  const [metric, setMetric] = useState<MetricKey>("messages");
  // Solo el dueño y los administradores lo ven: a los demás ni siquiera se les pregunta a la API.
  const report = useResource(() => (canEdit ? usageApi.get(period) : Promise.resolve(null)), [period, canEdit]);

  // Al reconectar, por si pasó algo mientras no había conexión.
  useEvents(["ready"], () => void report.reload());

  return (
    <main className="page">
      <div className="page-head page-head-row">
        <div>
          <h1>Consumo</h1>
          <p>Cuántos mensajes y respuestas de IA usa tu negocio.</p>
        </div>
        {canEdit && (
          <Button variant="secondary" icon="refresh" onClick={() => void report.reload()} disabled={report.loading}>
            Actualizar
          </Button>
        )}
      </div>

      {!canEdit ? (
        <section className="card">
          <EmptyState icon="chart" title="El consumo lo ve el dueño o un administrador">
            Pídele a alguien con ese rol que te cuente cómo va el mes.
          </EmptyState>
        </section>
      ) : (
        <>
          <div className="row usage-controls">
            <div className="segmented" role="group" aria-label="Periodo">
              {PERIODS.map((p) => (
                <button key={p.value} type="button" aria-pressed={period === p.value} onClick={() => setPeriod(p.value)}>
                  {p.label}
                </button>
              ))}
            </div>
            <div className="segmented" role="group" aria-label="Qué mostrar en el gráfico">
              {METRICS.map((m) => (
                <button key={m.key} type="button" aria-pressed={metric === m.key} onClick={() => setMetric(m.key)}>
                  {m.label}
                </button>
              ))}
            </div>
          </div>

          {report.error && (
            <Alert kind="error" title="No pudimos cargar el consumo">
              {report.error}{" "}
              <button type="button" className="link-button" onClick={() => void report.reload()}>
                Reintentar
              </button>
            </Alert>
          )}
          {report.loading && !report.data ? (
            <div className="center-box">
              <Spinner label="Cargando el consumo" size={28} />
            </div>
          ) : (
            report.data && <Report report={report.data} metric={metric} />
          )}
        </>
      )}
    </main>
  );
}
