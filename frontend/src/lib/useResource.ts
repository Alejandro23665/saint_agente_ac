import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError } from "../api/client";

export interface Resource<T> {
  data: T | null;
  error: string | null;
  loading: boolean; // la primera carga (después se recarga sin borrar lo que se ve)
  reload: () => Promise<void>;
  setData: (update: T | null | ((current: T | null) => T | null)) => void;
}

export function errorText(error: unknown): string {
  return error instanceof ApiError ? error.message : "Algo salió mal. Vuelve a intentar.";
}

/**
 * Carga algo de la API al abrir la pantalla (y cuando cambian deps). Las recargas
 * dejan lo que ya se ve hasta que llega lo nuevo; una respuesta vieja que llega tarde
 * no pisa a una más nueva.
 */
export function useResource<T>(load: () => Promise<T>, deps: unknown[] = []): Resource<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const latest = useRef(0);
  const loader = useRef(load);
  loader.current = load;

  const reload = useCallback(async () => {
    const ticket = ++latest.current;
    try {
      const value = await loader.current();
      if (ticket !== latest.current) return;
      setData(value);
      setError(null);
    } catch (e) {
      if (ticket !== latest.current) return;
      setError(errorText(e));
    } finally {
      if (ticket === latest.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    setLoading(true);
    void reload();
  }, deps); // deps las elige quien llama (como en useEffect)

  return { data, error, loading, reload, setData };
}
