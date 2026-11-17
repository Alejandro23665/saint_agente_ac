import { useId } from "react";
import type { InputHTMLAttributes, ReactNode, SelectHTMLAttributes, TextareaHTMLAttributes } from "react";

interface Common {
  label: string;
  hint?: ReactNode; // ayuda debajo del campo
  error?: string | null;
  // Contador "120 / 4096" (con maxLength y un valor de texto).
  counter?: boolean;
  // Tope que se muestra en el contador sin cortar lo que se escribe (si no hay maxLength).
  limit?: number;
}

function describedBy(id: string, hint: unknown, error: unknown): string | undefined {
  const ids = [hint ? `${id}-ayuda` : "", error ? `${id}-error` : ""].filter(Boolean);
  return ids.length ? ids.join(" ") : undefined;
}

function Foot({ id, hint, error, length, max }: { id: string; hint?: ReactNode; error?: string | null; length?: number; max?: number }) {
  if (!hint && !error && length === undefined) return null;
  return (
    <div className="field-foot">
      <span>
        {error && (
          <span id={`${id}-error`} className="field-error">
            {error}
          </span>
        )}
        {hint && !error && (
          <span id={`${id}-ayuda`} className="field-hint">
            {hint}
          </span>
        )}
      </span>
      {length !== undefined && max !== undefined && (
        <span className={length > max ? "field-count field-count-over" : "field-count"}>
          {length.toLocaleString("es")} / {max.toLocaleString("es")}
        </span>
      )}
    </div>
  );
}

export function TextField({ label, hint, error, counter, limit, id, className, ...rest }: Common & InputHTMLAttributes<HTMLInputElement>) {
  const auto = useId();
  const fieldId = id ?? auto;
  const length = counter ? String(rest.value ?? "").length : undefined;
  return (
    <div className="field">
      <label htmlFor={fieldId}>{label}</label>
      <input
        id={fieldId}
        className={["input", className].filter(Boolean).join(" ")}
        aria-invalid={error ? "true" : undefined}
        aria-describedby={describedBy(fieldId, hint, error)}
        {...rest}
      />
      <Foot id={fieldId} hint={hint} error={error} length={length} max={rest.maxLength ?? limit} />
    </div>
  );
}

export function TextArea({ label, hint, error, counter, limit, id, className, ...rest }: Common & TextareaHTMLAttributes<HTMLTextAreaElement>) {
  const auto = useId();
  const fieldId = id ?? auto;
  const length = counter ? String(rest.value ?? "").length : undefined;
  return (
    <div className="field">
      <label htmlFor={fieldId}>{label}</label>
      <textarea
        id={fieldId}
        className={["input textarea", className].filter(Boolean).join(" ")}
        aria-invalid={error ? "true" : undefined}
        aria-describedby={describedBy(fieldId, hint, error)}
        {...rest}
      />
      <Foot id={fieldId} hint={hint} error={error} length={length} max={rest.maxLength ?? limit} />
    </div>
  );
}

export function SelectField({
  label,
  hint,
  error,
  id,
  className,
  children,
  ...rest
}: Omit<Common, "counter" | "limit"> & SelectHTMLAttributes<HTMLSelectElement>) {
  const auto = useId();
  const fieldId = id ?? auto;
  return (
    <div className="field">
      <label htmlFor={fieldId}>{label}</label>
      <select
        id={fieldId}
        className={["input select", className].filter(Boolean).join(" ")}
        aria-invalid={error ? "true" : undefined}
        aria-describedby={describedBy(fieldId, hint, error)}
        {...rest}
      >
        {children}
      </select>
      <Foot id={fieldId} hint={hint} error={error} />
    </div>
  );
}

/** Interruptor encendido/apagado (un checkbox con forma de interruptor). */
export function Switch({
  label,
  hint,
  checked,
  onChange,
  disabled,
}: {
  label: string;
  hint?: ReactNode;
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
}) {
  const id = useId();
  return (
    <label className="switch" htmlFor={id}>
      <span className="switch-text">
        <strong>{label}</strong>
        {hint && <small>{hint}</small>}
      </span>
      <input
        id={id}
        type="checkbox"
        role="switch"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
      />
      <span className="switch-track" aria-hidden="true" />
    </label>
  );
}
