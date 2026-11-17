// Mi cuenta: /auth/me, /auth/password, /auth/logout-all (ver backend/app/api/auth.py).
import { api } from "./client";
import type { User } from "./types";

export const accountApi = {
  updateProfile: (fullName: string) => api<User>("/auth/me", { method: "PATCH", body: { full_name: fullName } }),
  // Cierra las demás sesiones; esta sigue abierta. Devuelve cuántas cerró.
  changePassword: (currentPassword: string, newPassword: string) =>
    api<{ closed_sessions: number }>("/auth/password", {
      method: "POST",
      body: { current_password: currentPassword, new_password: newPassword },
    }),
  // Cierra todas, incluida esta: después hay que volver a iniciar sesión.
  logoutAll: () => api<void>("/auth/logout-all", { method: "POST" }),
};
