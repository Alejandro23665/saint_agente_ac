import type { UsageDay } from "../api/types";
import { count } from "../lib/format";
import { barHeight, dayLabel, METRICS, seriesMax } from "../lib/usage";
import type { MetricKey } from "../lib/usage";

/**
 * Gráfico de barras por día, hecho con CSS (sin librerías). Las barras son decorativas: el
 * resumen va en el aria-label y los números exactos, en la tabla de abajo.
 */
export function UsageChart({ days, metric }: { days: UsageDay[]; metric: MetricKey }) {
  const config = METRICS.find((m) => m.key === metric) ?? METRICS[0];
  const max = seriesMax(days, config.series);
  const every = days.length > 16 ? 5 : days.length > 8 ? 2 : 1; // no amontonar las fechas

  if (max === 0) {
    return <p className="muted chart-empty">Sin actividad en este periodo.</p>;
  }

  const summary =
    `${config.label} por día, del ${dayLabel(days[0].day)} al ${dayLabel(days[days.length - 1].day)}. ` +
    `El día más alto: ${count(max)}.`;

  return (
    <div>
      <ul className="legend" aria-label="Leyenda">
        {config.series.length > 1 &&
          config.series.map((s) => (
            <li key={s.key}>
              <span className={`swatch swatch-${s.tone}`} aria-hidden="true" /> {s.label}
            </li>
          ))}
      </ul>
      <div className="chart" role="img" aria-label={summary}>
        {days.map((d, index) => (
          <div key={d.day} className="chart-col">
            <div className="chart-bars">
              {config.series.map((s) => (
                <span
                  key={s.key}
                  className={`bar bar-${s.tone}`}
                  style={{ height: `${barHeight(d[s.key], max)}%` }}
                  title={`${dayLabel(d.day)} · ${s.label}: ${count(d[s.key])}`}
                />
              ))}
            </div>
            <span className="chart-x" aria-hidden="true">
              {index % every === 0 ? dayLabel(d.day) : ""}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
