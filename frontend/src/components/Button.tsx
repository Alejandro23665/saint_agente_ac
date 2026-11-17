import type { ButtonHTMLAttributes } from "react";

import { Icon } from "./Icon";
import type { IconName } from "./Icon";

type Variant = "primary" | "secondary" | "danger" | "ghost";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  icon?: IconName;
  busy?: boolean; // muestra el círculo de carga y no deja volver a pulsar
  small?: boolean;
}

/** Botón del panel. Con busy, queda deshabilitado mientras termina la acción. */
export function Button({
  variant = "primary",
  icon,
  busy = false,
  small = false,
  type = "button",
  className,
  disabled,
  children,
  ...rest
}: ButtonProps) {
  const classes = ["btn", `btn-${variant}`, small ? "btn-small" : "", className ?? ""].filter(Boolean).join(" ");
  return (
    <button type={type} className={classes} disabled={disabled || busy} aria-busy={busy || undefined} {...rest}>
      {busy ? <Icon name="spinner" size={18} className="spin" /> : icon && <Icon name={icon} size={18} />}
      {children}
    </button>
  );
}
