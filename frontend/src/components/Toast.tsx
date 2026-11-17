/**
 * Avisos cortos abajo de la pantalla ("Guardado", "No se pudo enviar"). Se van solos a
 * los 4 segundos (los de error, a los 7).
 */
import { createContext, useCallback, useContext, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";

import { Icon } from "./Icon";

type Kind = "success" | "error" | "info";

interface Toast {
  id: number;
  kind: Kind;
  text: string;
}

interface ToastApi {
  show: (text: string, kind?: Kind) => void;
  success: (text: string) => void;
  error: (text: string) => void;
}

const ToastContext = createContext<ToastApi | null>(null);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const next = useRef(1);

  const dismiss = useCallback((id: number) => setToasts((list) => list.filter((t) => t.id !== id)), []);

  const show = useCallback(
    (text: string, kind: Kind = "info") => {
      const id = next.current++;
      setToasts((list) => [...list.slice(-2), { id, kind, text }]);
      window.setTimeout(() => dismiss(id), kind === "error" ? 7000 : 4000);
    },
    [dismiss],
  );

  const value = useMemo<ToastApi>(
    () => ({ show, success: (t) => show(t, "success"), error: (t) => show(t, "error") }),
    [show],
  );

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {toasts.map((toast) => (
          <div key={toast.id} className={`toast toast-${toast.kind}`}>
            <Icon name={toast.kind === "success" ? "check" : toast.kind === "error" ? "alert" : "lock"} size={18} />
            <span>{toast.text}</span>
            <button type="button" className="btn-icon" aria-label="Cerrar aviso" onClick={() => dismiss(toast.id)}>
              <Icon name="close" size={16} />
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

const SILENT: ToastApi = { show: () => {}, success: () => {}, error: () => {} };

export function useToast(): ToastApi {
  // Fuera del proveedor (algunas pruebas) los avisos simplemente no se ven.
  return useContext(ToastContext) ?? SILENT;
}
