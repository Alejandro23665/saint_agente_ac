/**
 * Conectar un número de WhatsApp con QR (o con un código, sin cámara).
 *
 * - Número nuevo (existing = null): primero se elige si recibe grupos; al generar el QR se
 *   crea el número.
 * - Número que ya existe (existing): pide un QR nuevo al abrir.
 * El QR vence en unos segundos, así que se renueva solo. Mientras la ventana está abierta
 * se pregunta el estado cada pocos segundos y con cada aviso numbers.changed; cuando
 * queda conectado, se avisa y la ventana se cierra sola.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError } from "../api/client";
import { numbersApi } from "../api/numbers";
import type { Qr, WhatsappNumber } from "../api/types";
import { useEvents } from "../layout/dashboard";
import { phone as formatPhone } from "../lib/format";
import { errorText } from "../lib/useResource";
import { Alert } from "./Alert";
import { Button } from "./Button";
import { Spinner } from "./Feedback";
import { Switch, TextField } from "./Field";
import { Modal } from "./Modal";
import { useToast } from "./Toast";

// En milisegundos. Es un objeto para que las pruebas lo puedan acortar.
export const timing = { poll: 4000, renew: 25000 };

type Mode = "qr" | "code";

interface Props {
  existing: WhatsappNumber | null;
  onClose: () => void;
  // Algo cambió (se creó el número o quedó conectado): la lista debe volver a cargarse.
  onChanged: () => void;
}

function formatCode(code: string): string {
  return code.length === 8 ? `${code.slice(0, 4)}-${code.slice(4)}` : code;
}

export function ConnectNumberModal({ existing, onClose, onChanged }: Props) {
  const toast = useToast();
  const [number, setNumber] = useState<WhatsappNumber | null>(existing);
  const [qr, setQr] = useState<Qr | null>(null);
  const [mode, setMode] = useState<Mode>("qr");
  const [groups, setGroups] = useState(false);
  const [phoneText, setPhoneText] = useState("");
  const [phoneError, setPhoneError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const numberRef = useRef<WhatsappNumber | null>(existing);
  const groupsRef = useRef(groups);
  groupsRef.current = groups;
  const callbacks = useRef({ onClose, onChanged });
  callbacks.current = { onClose, onChanged };
  const requesting = useRef(false);
  const checking = useRef(false);
  const finished = useRef(false);
  const alive = useRef(true);

  const update = useCallback((next: WhatsappNumber) => {
    numberRef.current = next;
    setNumber(next);
  }, []);

  const done = useCallback(() => {
    if (finished.current) return;
    finished.current = true;
    toast.success("Número conectado");
    callbacks.current.onChanged();
    callbacks.current.onClose();
  }, [toast]);

  const request = useCallback(
    async (phone?: string, silent = false) => {
      if (requesting.current || finished.current) return;
      requesting.current = true;
      if (!silent) setLoading(true);
      try {
        const current = numberRef.current;
        const result = current
          ? await numbersApi.newQr(current.id, phone)
          : await numbersApi.connect(groupsRef.current);
        if (!alive.current) return;
        if (!current) callbacks.current.onChanged(); // el número nuevo ya existe
        update(result.number);
        if (result.number.status === "open") {
          done();
          return;
        }
        setQr(result.qr);
        setError(null);
        setPhoneError(null);
      } catch (e) {
        if (!alive.current) return;
        if (phone && e instanceof ApiError && e.status === 422) setPhoneError(e.message);
        else if (!silent) setError(errorText(e));
      } finally {
        requesting.current = false;
        if (alive.current) setLoading(false);
      }
    },
    [update, done],
  );

  const check = useCallback(async () => {
    const current = numberRef.current;
    if (!current || checking.current || finished.current) return;
    checking.current = true;
    try {
      const fresh = await numbersApi.refresh(current.id);
      if (!alive.current) return;
      update(fresh);
      if (fresh.status === "open") done();
    } catch {
      // Un error de red aquí no importa: se vuelve a preguntar en unos segundos.
    } finally {
      checking.current = false;
    }
  }, [update, done]);

  useEffect(() => {
    alive.current = true;
    if (existing) void request();
    return () => {
      alive.current = false;
    };
  }, []); // solo al abrir

  const numberId = number?.id;
  useEffect(() => {
    if (!numberId) return;
    const timer = window.setInterval(() => void check(), timing.poll);
    return () => window.clearInterval(timer);
  }, [numberId, check]);

  useEffect(() => {
    if (!numberId || mode !== "qr") return;
    const timer = window.setInterval(() => void request(undefined, true), timing.renew);
    return () => window.clearInterval(timer);
  }, [numberId, mode, request]);

  useEvents(["numbers.changed"], () => void check());

  const started = number !== null;
  const banned = number?.status === "banned";

  return (
    <Modal
      title="Conectar un número de WhatsApp"
      onClose={onClose}
      wide={started}
      footer={
        !started ? (
          <>
            <Button variant="secondary" onClick={onClose}>
              Cancelar
            </Button>
            <Button onClick={() => void request()} busy={loading}>
              Generar QR
            </Button>
          </>
        ) : (
          <Button variant="secondary" onClick={onClose}>
            Cerrar
          </Button>
        )
      }
    >
      {error && (
        <Alert kind="error" title="No pudimos conectar">
          {error}
        </Alert>
      )}

      {!started && (
        <>
          <p className="muted">
            Vas a vincular el WhatsApp de tu negocio a Saint. Ten el teléfono a la mano: después de generar el QR lo
            escaneas desde WhatsApp.
          </p>
          <Switch
            label="Recibir también los mensajes de grupos"
            hint="Si lo dejas apagado, el agente solo atiende conversaciones directas. Lo puedes cambiar después."
            checked={groups}
            onChange={setGroups}
          />
        </>
      )}

      {started && banned && (
        <Alert kind="error" title="WhatsApp bloqueó este número">
          Saint no puede usarlo. Revisa tu cuenta de WhatsApp desde el teléfono.
        </Alert>
      )}

      {started && !banned && (
        <>
          {number.phone && <p className="muted">Número: {formatPhone(number.phone)}</p>}
          <div className="segmented" role="group" aria-label="Cómo vincular">
            <button type="button" aria-pressed={mode === "qr"} onClick={() => setMode("qr")}>
              Escanear QR
            </button>
            <button type="button" aria-pressed={mode === "code"} onClick={() => setMode("code")}>
              Usar un código
            </button>
          </div>

          {mode === "qr" ? (
            <div className="connect-grid">
              <div className="qr-box">
                {qr?.image ? (
                  <img src={qr.image} alt="Código QR para vincular WhatsApp" width={240} height={240} />
                ) : (
                  <Spinner label="Generando el QR" size={28} />
                )}
              </div>
              <div className="stack">
                <ol className="how-to">
                  <li>Abre WhatsApp en el teléfono del negocio.</li>
                  <li>
                    Entra a <strong>Ajustes → Dispositivos vinculados</strong>.
                  </li>
                  <li>
                    Toca <strong>Vincular un dispositivo</strong> y apunta la cámara a este QR.
                  </li>
                </ol>
                <p className="muted">El QR se renueva solo cada pocos segundos. Cuando lo escanees, esta ventana se cierra.</p>
                <div>
                  <Button variant="secondary" small icon="refresh" onClick={() => void request()} busy={loading}>
                    Generar otro QR
                  </Button>
                </div>
              </div>
            </div>
          ) : (
            <div className="stack">
              <ol className="how-to">
                <li>Escribe aquí el número del teléfono, con código de país.</li>
                <li>
                  En WhatsApp entra a <strong>Ajustes → Dispositivos vinculados → Vincular un dispositivo</strong>.
                </li>
                <li>
                  Elige <strong>Vincular con el número de teléfono</strong> y escribe el código que verás abajo.
                </li>
              </ol>
              <div className="row">
                <div style={{ flex: 1, minWidth: 220 }}>
                  <TextField
                    label="Número del teléfono"
                    placeholder="+58 412 1234567"
                    inputMode="tel"
                    autoComplete="tel"
                    value={phoneText}
                    onChange={(e) => setPhoneText(e.target.value)}
                    error={phoneError}
                  />
                </div>
                <Button onClick={() => void request(phoneText)} busy={loading} disabled={!phoneText.trim()}>
                  Obtener código
                </Button>
              </div>
              {qr?.pairing_code && (
                <div className="pairing" aria-live="polite">
                  <span>Tu código</span>
                  <strong aria-label={`Código ${qr.pairing_code.split("").join(" ")}`}>{formatCode(qr.pairing_code)}</strong>
                </div>
              )}
            </div>
          )}
        </>
      )}
    </Modal>
  );
}
