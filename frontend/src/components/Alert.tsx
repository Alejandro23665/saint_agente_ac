import type { ReactNode } from "react";

import { Icon } from "./Icon";

const KINDS = {
  error: { icon: "alert", role: "alert" },
  warn: { icon: "clock", role: "alert" },
  info: { icon: "lock", role: "status" },
} as const;

/** Mensaje encima del formulario: error (rojo), espera (ámbar) o aviso (azul). */
export function Alert({ kind, title, children }: { kind: keyof typeof KINDS; title: string; children: ReactNode }) {
  return (
    <div role={KINDS[kind].role} className={`alert alert-${kind}`}>
      <Icon name={KINDS[kind].icon} className="alert-icon" />
      <div>
        <strong>{title}</strong>
        <span>{children}</span>
      </div>
    </div>
  );
}
