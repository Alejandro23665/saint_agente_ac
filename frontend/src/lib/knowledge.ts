/** Piezas puras del conocimiento: leer un archivo de texto, sugerir el título y detectar atascos. */
import type { KnowledgeSource } from "../api/types";
import { LIMITS } from "../api/types";
import { count } from "./format";

export const MAX_FILE_BYTES = 1_000_000;
const ALLOWED = /\.(txt|md|markdown)$/i;

/** "horarios_de_la_tienda.md" -> "horarios de la tienda". */
export function titleFromFilename(name: string): string {
  const base = name.replace(/\.[^.]+$/, "").replace(/[_-]+/g, " ").replace(/\s+/g, " ").trim();
  return base.slice(0, LIMITS.knowledgeTitle);
}

function readAsText(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ""));
    reader.onerror = () => reject(new Error("No pudimos leer el archivo. Prueba con otro."));
    reader.readAsText(file, "utf-8");
  });
}

/** Lee un .txt o .md en el navegador. Los errores son frases listas para mostrar. */
export async function readTextFile(file: File): Promise<{ title: string; text: string }> {
  if (!ALLOWED.test(file.name)) throw new Error("Solo se pueden cargar archivos .txt o .md.");
  if (file.size > MAX_FILE_BYTES) throw new Error("El archivo pesa más de 1 MB. Divídelo en varios documentos.");
  const text = await readAsText(file);
  if (!text.trim()) throw new Error("El archivo está vacío.");
  if (text.length > LIMITS.knowledgeChars) {
    throw new Error(`El texto es demasiado grande (${count(text.length)} caracteres; máximo ${count(LIMITS.knowledgeChars)}).`);
  }
  return { title: titleFromFilename(file.name), text };
}

/**
 * Un documento que sigue "procesando" mucho tiempo: lo normal es que tarde segundos, así que
 * quizá no se pudo encolar. Se permite reintentar.
 */
export function isStuck(source: Pick<KnowledgeSource, "status" | "updated_at">, now: number = Date.now(), minutes = 5): boolean {
  return source.status === "processing" && now - Date.parse(source.updated_at) > minutes * 60_000;
}

export function canRetry(source: Pick<KnowledgeSource, "status" | "updated_at">, now: number = Date.now()): boolean {
  return source.status === "failed" || isStuck(source, now);
}

/** Cuántos fragmentos y de qué tamaño, en una línea. */
export function sizeLine(source: Pick<KnowledgeSource, "chunk_count" | "characters">): string {
  const chunks = source.chunk_count === 1 ? "1 fragmento" : `${count(source.chunk_count)} fragmentos`;
  return `${chunks} · ${count(source.characters)} caracteres`;
}
