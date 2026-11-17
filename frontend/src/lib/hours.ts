/**
 * El horario de atención en el panel. La API guarda {"mon": [["08:00","12:00"]], ...} en la
 * hora del negocio. Aquí se convierte a un borrador editable y se valida con las mismas
 * reglas que el servidor (hasta 3 tramos por día, el cierre después de la apertura y sin
 * tramos que se pisen), para avisar antes de guardar.
 *
 * Ojo con dos casos distintos:
 * - horario vacío ({}): el negocio cuenta como abierto siempre;
 * - horario con todos los días vacíos: el negocio cuenta como cerrado siempre.
 */
import type { DayKey, HoursIn } from "../api/types";

export const DAYS: { key: DayKey; label: string }[] = [
  { key: "mon", label: "Lunes" },
  { key: "tue", label: "Martes" },
  { key: "wed", label: "Miércoles" },
  { key: "thu", label: "Jueves" },
  { key: "fri", label: "Viernes" },
  { key: "sat", label: "Sábado" },
  { key: "sun", label: "Domingo" },
];

export const MAX_RANGES = 3;
export const DEFAULT_RANGE: TimeRange = { start: "08:00", end: "18:00" };

export interface TimeRange {
  start: string; // HH:MM
  end: string;
}

export type Draft = Record<DayKey, TimeRange[]>;

const TIME = /^([01]\d|2[0-3]):[0-5]\d$/;

const byStart = (a: TimeRange, b: TimeRange) => (a.start < b.start ? -1 : a.start > b.start ? 1 : 0);

export function emptyDraft(): Draft {
  return { mon: [], tue: [], wed: [], thu: [], fri: [], sat: [], sun: [] };
}

/** De lunes a viernes de 8:00 a 18:00: el punto de partida al activar el horario. */
export function weekdayDraft(): Draft {
  const draft = emptyDraft();
  for (const day of ["mon", "tue", "wed", "thu", "fri"] as const) draft[day] = [{ ...DEFAULT_RANGE }];
  return draft;
}

/** Lo que devuelve la API, como borrador. El campo de hora no admite "24:00": se muestra 23:59. */
export function toDraft(days: HoursIn["days"] | undefined): Draft {
  const draft = emptyDraft();
  for (const { key } of DAYS) {
    draft[key] = (days?.[key] ?? [])
      .map(([start, end]) => ({ start, end: end === "24:00" ? "23:59" : end }))
      .sort(byStart);
  }
  return draft;
}

/** Lo que se manda a la API: siempre los 7 días, con [] en los cerrados y los tramos en orden. */
export function fromDraft(draft: Draft): Record<DayKey, string[][]> {
  const days = {} as Record<DayKey, string[][]>;
  for (const { key } of DAYS) days[key] = [...draft[key]].sort(byStart).map((r) => [r.start, r.end]);
  return days;
}

/** Copia los tramos de un día a todos los demás. */
export function copyDay(draft: Draft, from: DayKey): Draft {
  const next = emptyDraft();
  for (const { key } of DAYS) next[key] = draft[from].map((r) => ({ ...r }));
  return next;
}

export function validateDay(ranges: TimeRange[]): string | null {
  if (ranges.length > MAX_RANGES) return `Hasta ${MAX_RANGES} tramos por día.`;
  for (const r of ranges) {
    if (!TIME.test(r.start) || !TIME.test(r.end)) return "Escribe la hora de apertura y la de cierre.";
    if (r.start >= r.end) return "El cierre debe ser después de la apertura.";
  }
  const sorted = [...ranges].sort(byStart);
  for (let i = 1; i < sorted.length; i += 1) {
    if (sorted[i].start < sorted[i - 1].end) return "Los tramos se pisan.";
  }
  return null;
}

export function validateDraft(draft: Draft): Partial<Record<DayKey, string>> {
  const errors: Partial<Record<DayKey, string>> = {};
  for (const { key } of DAYS) {
    const problem = validateDay(draft[key]);
    if (problem) errors[key] = problem;
  }
  return errors;
}

/** ¿Ningún día tiene tramos? Guardado así, el negocio figuraría siempre cerrado. */
export function allClosed(draft: Draft): boolean {
  return DAYS.every(({ key }) => draft[key].length === 0);
}

/** El servidor junta los espacios repetidos del mensaje; se compara igual. */
export function normalizeMessage(text: string | null | undefined): string {
  return (text ?? "").split(/\s+/).filter(Boolean).join(" ");
}

/** "08:00–12:00, 14:00–18:00" o "Cerrado". */
export function describeDay(ranges: TimeRange[]): string {
  return ranges.length ? [...ranges].sort(byStart).map((r) => `${r.start}–${r.end}`).join(", ") : "Cerrado";
}
