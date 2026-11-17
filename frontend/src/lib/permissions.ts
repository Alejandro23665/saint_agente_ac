/**
 * Qué puede hacer cada rol. Es solo para mostrar u ocultar botones: la API vuelve a
 * comprobarlo en cada petición (ver backend/app/services/members.py).
 */
import type { Member, Role } from "../api/types";

/** ¿Puede quien tiene este rol invitar, cambiar de rol o quitar a alguien con el otro rol? */
export function canManage(actor: Role | null, target: Role): boolean {
  if (actor === "owner") return true;
  if (actor === "admin") return target === "agent";
  return false;
}

/** Los roles con los que puede invitar, del menos al más poderoso. */
export function invitableRoles(actor: Role | null): Role[] {
  if (actor === "owner") return ["agent", "admin", "owner"];
  if (actor === "admin") return ["agent"];
  return [];
}

/** Cambiar el rol de alguien solo tiene sentido para el dueño: un administrador solo gestiona agentes. */
export function canChangeRoles(actor: Role | null): boolean {
  return actor === "owner";
}

/** Cambios que dan o quitan el control total del negocio: se piden con confirmación. */
export function needsConfirmation(from: Role, to: Role): boolean {
  return from === "owner" || to === "owner";
}

export function ownerCount(members: Pick<Member, "role">[]): number {
  return members.filter((m) => m.role === "owner").length;
}

export const ROLE_HINTS: Record<Role, string> = {
  owner: "Control total: equipo, números, agente y borrar números.",
  admin: "Gestiona el negocio y a los agentes; no toca a dueños ni administradores.",
  agent: "Atiende conversaciones. Ve todo, pero no cambia la configuración.",
};

export interface MatrixRow {
  action: string;
  owner: string;
  admin: string;
  agent: string;
}

export const ROLE_MATRIX: MatrixRow[] = [
  { action: "Ver la bandeja, los números, el agente y el equipo", owner: "Sí", admin: "Sí", agent: "Sí" },
  { action: "Contestar, tomar y devolver conversaciones", owner: "Sí", admin: "Sí", agent: "Sí" },
  { action: "Asignar y cerrar conversaciones", owner: "Sí", admin: "Sí", agent: "Cerrar, sí; asignar, no" },
  { action: "Conectar, desconectar y configurar números", owner: "Sí", admin: "Sí", agent: "No" },
  { action: "Borrar un número", owner: "Sí", admin: "No", agent: "No" },
  { action: "Cambiar el agente, el horario y el conocimiento", owner: "Sí", admin: "Sí", agent: "No" },
  { action: "Cambiar el nombre, el país y la zona horaria", owner: "Sí", admin: "Sí", agent: "No" },
  { action: "Invitar, cambiar de rol o quitar a alguien", owner: "A todos", admin: "Solo agentes", agent: "No" },
  { action: "Ver y anular invitaciones", owner: "Todas", admin: "Las de agentes", agent: "No" },
  { action: "Salir del negocio", owner: "Sí (si queda otro dueño)", admin: "Sí", agent: "Sí" },
];
