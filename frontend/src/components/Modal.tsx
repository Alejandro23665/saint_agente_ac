import { useEffect, useId, useRef } from "react";
import type { ReactNode } from "react";
import { createPortal } from "react-dom";

import { Icon } from "./Icon";

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

interface ModalProps {
  title: string;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode; // los botones de abajo
  wide?: boolean;
  // false: no se cierra con Escape ni tocando fuera (mientras se guarda algo).
  dismissable?: boolean;
}

/**
 * Ventana encima de la pantalla. Escape o tocar fuera la cierran; el foco queda dentro
 * mientras está abierta y vuelve al botón que la abrió al cerrarla.
 */
export function Modal({ title, onClose, children, footer, wide = false, dismissable = true }: ModalProps) {
  const titleId = useId();
  const box = useRef<HTMLDivElement>(null);
  const close = useRef(onClose);
  close.current = onClose;
  const canDismiss = useRef(dismissable);
  canDismiss.current = dismissable;

  useEffect(() => {
    const before = document.activeElement as HTMLElement | null;
    const first = box.current?.querySelector<HTMLElement>("[data-autofocus]") ?? box.current?.querySelector<HTMLElement>(FOCUSABLE);
    (first ?? box.current)?.focus();

    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape" && canDismiss.current) {
        event.stopPropagation();
        close.current();
      }
      if (event.key !== "Tab" || !box.current) return;
      const items = [...box.current.querySelectorAll<HTMLElement>(FOCUSABLE)];
      if (!items.length) return;
      const [start, end] = [items[0], items[items.length - 1]];
      if (event.shiftKey && document.activeElement === start) {
        event.preventDefault();
        end.focus();
      } else if (!event.shiftKey && document.activeElement === end) {
        event.preventDefault();
        start.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = overflow;
      before?.focus?.();
    };
  }, []);

  return createPortal(
    <div
      className="modal-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && canDismiss.current) close.current();
      }}
    >
      <div
        ref={box}
        className={wide ? "modal modal-wide" : "modal"}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
      >
        <header className="modal-head">
          <h2 id={titleId}>{title}</h2>
          {dismissable && (
            <button type="button" className="btn-icon" aria-label="Cerrar" onClick={() => close.current()}>
              <Icon name="close" />
            </button>
          )}
        </header>
        <div className="modal-body">{children}</div>
        {footer && <footer className="modal-foot">{footer}</footer>}
      </div>
    </div>,
    document.body,
  );
}
