/** Piezas puras de la pantalla Consumo: periodos, métricas del gráfico y formatos. */
import type { UsageDay, UsagePeriod, UsageReport } from "../api/types";
import { count, shortDate } from "./format";

export const PERIODS: { value: UsagePeriod; label: string }[] = [
  { value: "this_month", label: "Este mes" },
  { value: "last_month", label: "Mes pasado" },
  { value: "last_7_days", label: "Últimos 7 días" },
  { value: "last_30_days", label: "Últimos 30 días" },
];

export type MetricKey = "messages" | "ai" | "tokens" | "conversations";

export interface Series {
  key: keyof Omit<UsageDay, "day">;
  label: string;
  tone: "a" | "b";
}

export const METRICS: { key: MetricKey; label: string; series: Series[] }[] = [
  {
    key: "messages",
    label: "Mensajes",
    series: [
      { key: "inbound_messages", label: "Recibidos", tone: "a" },
      { key: "outbound_messages", label: "Enviados", tone: "b" },
    ],
  },
  { key: "ai", label: "Respuestas de IA", series: [{ key: "ai_replies", label: "Respuestas de IA", tone: "a" }] },
  {
    key: "tokens",
    label: "Tokens de IA",
    series: [
      { key: "input_tokens", label: "Entrada", tone: "a" },
      { key: "output_tokens", label: "Salida", tone: "b" },
    ],
  },
  { key: "conversations", label: "Conversaciones nuevas", series: [{ key: "conversations", label: "Conversaciones nuevas", tone: "a" }] },
];

/** "2026-11-16" -> fecha local (sin pasar por UTC, que correría el día). */
export function parseDay(day: string): Date {
  const [y, m, d] = day.split("-").map(Number);
  return new Date(y, m - 1, d);
}

/** "16 nov". */
export function dayLabel(day: string): string {
  return shortDate(parseDay(day));
}

/** El valor más alto de las series en cualquier día (para la altura de las barras). */
export function seriesMax(days: UsageDay[], series: Series[]): number {
  return Math.max(0, ...days.flatMap((d) => series.map((s) => d[s.key])));
}

/** Altura de una barra en % del alto del gráfico; un valor pequeño pero real nunca desaparece. */
export function barHeight(value: number, max: number): number {
  if (value <= 0 || max <= 0) return 0;
  return Math.max(3, Math.round((value / max) * 100));
}

/** 1.234 -> "1.234"; 12.345 -> "12,3 mil"; 45.000 -> "45 mil"; 1.500.000 -> "1,5 M". */
export function compact(n: number): string {
  const short = (value: number) => value.toFixed(1).replace(/\.0$/, "").replace(".", ",");
  if (n >= 1_000_000) return `${short(n / 1_000_000)} M`;
  if (n >= 10_000) return `${short(n / 1_000)} mil`;
  return count(n);
}

/** "US$ 3,50". */
export function money(usd: number): string {
  return `US$ ${usd.toLocaleString("es", { minimumFractionDigits: 2, maximumFractionDigits: 4 })}`;
}

/** "Del 1 al 16 de noviembre" (o entre dos meses: "Del 17 dic al 15 ene"). */
export function periodLine(start: string, end: string): string {
  const a = parseDay(start);
  const b = parseDay(end);
  if (start === end) return `El ${a.getDate()} de ${a.toLocaleDateString("es", { month: "long" })}`;
  if (a.getMonth() === b.getMonth() && a.getFullYear() === b.getFullYear()) {
    return `Del ${a.getDate()} al ${b.getDate()} de ${b.toLocaleDateString("es", { month: "long" })}`;
  }
  return `Del ${shortDate(a)} al ${shortDate(b)}`;
}

export function totalTokens(report: Pick<UsageReport, "totals">): number {
  return report.totals.input_tokens + report.totals.output_tokens;
}

/** ¿No pasó nada en todo el periodo? */
export function isEmpty(report: Pick<UsageReport, "totals">): boolean {
  const t = report.totals;
  return t.conversations + t.inbound_messages + t.outbound_messages + t.ai_replies + t.input_tokens + t.output_tokens === 0;
}

/** Qué decir sobre desde cuándo hay datos, o null si el periodo está completo. */
export function trackingNotice(report: Pick<UsageReport, "start" | "tracking_since">): string | null {
  if (report.tracking_since === null) {
    return "Todavía no hay consumo registrado. Se empieza a contar con el primer mensaje que llegue o salga.";
  }
  if (report.tracking_since > report.start) {
    return `Solo hay datos desde el ${dayLabel(report.tracking_since)}. Antes de esa fecha el consumo no se registraba.`;
  }
  return null;
}
