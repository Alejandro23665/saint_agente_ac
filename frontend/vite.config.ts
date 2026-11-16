/// <reference types="vitest" />
import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

// En desarrollo el panel y la API se ven como una sola dirección (http://127.0.0.1:5173):
// Vite pasa a la API todo lo que empieza por una de sus rutas. Así la cookie de
// renovación funciona sin configurar CORS y nada cambia al pasar a producción (Caddy
// hará lo mismo en el bloque 7.2).
const API_ROUTES = "^/(auth|tenant|numbers|conversations|agent|hours|knowledge|events|health)(/|\\?|$)";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, ".", "");
  const api = env.SAINT_API_URL || "http://127.0.0.1:8000";
  return {
    plugins: [react()],
    server: {
      host: "127.0.0.1",
      port: 5173,
      strictPort: true,
      proxy: { [API_ROUTES]: { target: api, changeOrigin: false } },
    },
    test: {
      environment: "jsdom",
      globals: true,
      setupFiles: ["./src/test-setup.ts"],
    },
  };
});
