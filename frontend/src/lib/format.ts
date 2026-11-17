/** Fechas, números y textos tal como se muestran en el panel (español). */

const LOCALE = "es";

function toDate(value: string | Date): Date {
  return value instanceof Date ? value : new Date(value);
}

/** "ahora", "hace 5 min", "hace 3 h", "ayer", "hace 4 días" o la fecha. */
export function timeAgo(value: string | Date | null | undefined, now: Date = new Date()): string {
  if (!value) return "";
  const date = toDate(value);
  const seconds = Math.round((now.getTime() - date.getTime()) / 1000);
  if (seconds < 45) return "ahora";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `hace ${minutes} min`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `hace ${hours} h`;
  const days = Math.round(hours / 24);
  if (days === 1) return "ayer";
  if (days < 7) return `hace ${days} días`;
  return shortDate(date, now);
}

/** "14 nov" (o "14 nov 2025" si es de otro año). */
export function shortDate(value: string | Date, now: Date = new Date()): string {
  const date = toDate(value);
  return date.toLocaleDateString(LOCALE, {
    day: "numeric",
    month: "short",
    year: date.getFullYear() === now.getFullYear() ? undefined : "numeric",
  });
}

/** "14 nov 2026, 15:30". */
export function dateTime(value: string | Date): string {
  return toDate(value).toLocaleString(LOCALE, {
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** "15:30" (para las burbujas de la bandeja). */
export function clock(value: string | Date): string {
  return toDate(value).toLocaleTimeString(LOCALE, { hour: "2-digit", minute: "2-digit" });
}

/** 12345 -> "12.345" y 5000 -> "5.000" (el español suele omitir el punto en 4 cifras; en una tabla se ve raro). */
export function count(value: number): string {
  // "always" lo admiten los navegadores actuales, pero los tipos de TypeScript aún no lo conocen.
  return value.toLocaleString(LOCALE, { useGrouping: "always" } as unknown as Intl.NumberFormatOptions);
}

/** plural(1, "mensaje") -> "1 mensaje"; plural(3, "mensaje") -> "3 mensajes". */
export function plural(n: number, one: string, many = `${one}s`): string {
  return `${count(n)} ${n === 1 ? one : many}`;
}

/** "+584121234567" -> "+58 412 1234567" (si no se reconoce, se deja igual). */
export function phone(value: string | null | undefined): string {
  if (!value) return "";
  const digits = value.replace(/[^\d+]/g, "");
  const venezuela = /^\+58(\d{3})(\d{7})$/.exec(digits);
  if (venezuela) return `+58 ${venezuela[1]} ${venezuela[2]}`;
  return value;
}

export const ROLE_LABELS = {
  owner: "Dueño",
  admin: "Administrador",
  agent: "Agente",
} as const;

export function roleLabel(role: string): string {
  return (ROLE_LABELS as Record<string, string>)[role] ?? role;
}
