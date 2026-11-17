import { Navigate, Route, Routes, useLocation } from "react-router-dom";

import { useAuth } from "./auth/session";
import { Logo } from "./components/Logo";
import { AppLayout } from "./layout/AppLayout";
import { AcceptInvitation } from "./pages/AcceptInvitation";
import { AgentPage } from "./pages/Agent";
import { ChooseBusiness } from "./pages/ChooseBusiness";
import { Home } from "./pages/Home";
import { Inbox } from "./pages/Inbox";
import { Knowledge } from "./pages/Knowledge";
import { Login } from "./pages/Login";
import { Numbers } from "./pages/Numbers";
import { Settings } from "./pages/Settings";
import { Team } from "./pages/Team";
import { Usage } from "./pages/Usage";

function Splash() {
  return (
    <div className="splash" role="status" aria-label="Cargando el panel">
      <Logo width={150} />
    </div>
  );
}

export function App() {
  const { status, session } = useAuth();
  const location = useLocation();

  if (status === "loading") return <Splash />;

  // El enlace de invitación se abre con o sin sesión (y no debe perder el #token).
  if (location.pathname === "/invitacion") return <AcceptInvitation />;

  if (status === "anonymous") {
    return location.pathname === "/iniciar-sesion" ? (
      <Login />
    ) : (
      <Navigate to="/iniciar-sesion" replace state={{ from: location.pathname }} />
    );
  }

  if (!session?.active_tenant) {
    return location.pathname === "/elegir-negocio" ? <ChooseBusiness /> : <Navigate to="/elegir-negocio" replace />;
  }

  const back = (location.state as { from?: string } | null)?.from;
  return (
    <Routes>
      <Route path="/iniciar-sesion" element={<Navigate to={back && back !== "/iniciar-sesion" ? back : "/"} replace />} />
      <Route path="/elegir-negocio" element={session.tenants.length > 1 ? <ChooseBusiness /> : <Navigate to="/" replace />} />
      <Route element={<AppLayout />}>
        <Route index element={<Home />} />
        <Route path="numeros" element={<Numbers />} />
        <Route path="bandeja/:id?" element={<Inbox />} />
        <Route path="agente" element={<AgentPage />} />
        <Route path="conocimiento" element={<Knowledge />} />
        <Route path="equipo" element={<Team />} />
        <Route path="ajustes" element={<Settings />} />
        <Route path="consumo" element={<Usage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
