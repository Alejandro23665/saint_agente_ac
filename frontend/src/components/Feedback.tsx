import type { ReactNode } from "react";

import { Icon } from "./Icon";
import type { IconName } from "./Icon";

/** Lo que se ve cuando una lista está vacía: qué es y qué hacer. */
export function EmptyState({ icon, title, children, action }: { icon: IconName; title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="empty">
      <span className="empty-icon">
        <Icon name={icon} size={26} />
      </span>
      <strong>{title}</strong>
      {children && <p className="muted">{children}</p>}
      {action}
    </div>
  );
}

/** Círculo de carga con su texto para lectores de pantalla. */
export function Spinner({ label = "Cargando", size = 22 }: { label?: string; size?: number }) {
  return (
    <span className="spinner" role="status">
      <Icon name="spinner" size={size} className="spin" />
      <span className="visually-hidden">{label}</span>
    </span>
  );
}

export type Tone = "success" | "warn" | "danger" | "ice" | "muted";

/** Etiqueta de estado: Conectado, Procesando, Falló... */
export function StatusPill({ tone, children, dot = false }: { tone: Tone; children: ReactNode; dot?: boolean }) {
  return (
    <span className={`pill pill-${tone}`}>
      {dot && <span className={tone === "muted" ? "dot dot-off" : `dot dot-${tone}`} />}
      {children}
    </span>
  );
}
