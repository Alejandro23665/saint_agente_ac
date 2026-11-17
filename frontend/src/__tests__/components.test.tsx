import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";

import { ApiError, query } from "../api/client";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { TextArea } from "../components/Field";
import { ToastProvider, useToast } from "../components/Toast";
import { phone, plural, timeAgo } from "../lib/format";

test("confirmar algo que no se deshace exige escribir el texto", async () => {
  const onConfirm = vi.fn().mockResolvedValue(undefined);
  const onClose = vi.fn();
  render(
    <ConfirmDialog title="Borrar número" confirmLabel="Borrar" danger typeToConfirm="+58 412 1234567" onConfirm={onConfirm} onClose={onClose}>
      Se borra de WhatsApp.
    </ConfirmDialog>,
  );
  const user = userEvent.setup();
  const button = screen.getByRole("button", { name: "Borrar" });
  expect(button).toBeDisabled();
  await user.type(screen.getByLabelText("Para confirmar, escribe: +58 412 1234567"), "+58 412 1234567");
  expect(button).toBeEnabled();
  await user.click(button);
  expect(onConfirm).toHaveBeenCalledOnce();
  expect(onClose).toHaveBeenCalledOnce();
});

test("si la acción falla, el error se ve y la ventana sigue abierta", async () => {
  const onClose = vi.fn();
  render(
    <ConfirmDialog
      title="Desconectar"
      confirmLabel="Desconectar"
      onConfirm={() => Promise.reject(new ApiError(502, "No se pudo hablar con WhatsApp"))}
      onClose={onClose}
    >
      El teléfono se desvincula.
    </ConfirmDialog>,
  );
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Desconectar" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("No se pudo hablar con WhatsApp");
  expect(onClose).not.toHaveBeenCalled();
});

test("Escape cierra la ventana", async () => {
  const onClose = vi.fn();
  render(
    <ConfirmDialog title="Cerrar" confirmLabel="Sí" onConfirm={() => {}} onClose={onClose}>
      ¿Cerrar?
    </ConfirmDialog>,
  );
  await userEvent.setup().keyboard("{Escape}");
  expect(onClose).toHaveBeenCalledOnce();
});

test("el contador de caracteres avisa al pasarse", async () => {
  function Box() {
    const [text, setText] = useState("hola");
    return <TextArea label="Mensaje" counter maxLength={10} value={text} onChange={(e) => setText(e.target.value)} />;
  }
  render(<Box />);
  expect(screen.getByText("4 / 10")).toBeInTheDocument();
});

test("los avisos cortos aparecen en la pantalla", async () => {
  function Save() {
    const toast = useToast();
    return (
      <button type="button" onClick={() => toast.success("Guardado")}>
        Guardar
      </button>
    );
  }
  render(
    <ToastProvider>
      <Save />
    </ToastProvider>,
  );
  await userEvent.setup().click(screen.getByRole("button", { name: "Guardar" }));
  expect(screen.getByRole("status")).toHaveTextContent("Guardado");
});

test("formatos", () => {
  const now = new Date("2026-11-16T12:00:00Z");
  expect(timeAgo("2026-11-16T11:59:50Z", now)).toBe("ahora");
  expect(timeAgo("2026-11-16T11:55:00Z", now)).toBe("hace 5 min");
  expect(timeAgo("2026-11-16T09:00:00Z", now)).toBe("hace 3 h");
  expect(timeAgo("2026-11-15T12:00:00Z", now)).toBe("ayer");
  expect(timeAgo(null, now)).toBe("");
  expect(plural(1, "mensaje")).toBe("1 mensaje");
  expect(plural(3, "mensaje")).toBe("3 mensajes");
  expect(phone("+584121234567")).toBe("+58 412 1234567");
  expect(phone("+15551234567")).toBe("+15551234567");
  expect(query({ status: "open", mine: false, q: "", number_id: undefined, limit: 30 })).toBe("?status=open&limit=30");
  expect(query({})).toBe("");
});
