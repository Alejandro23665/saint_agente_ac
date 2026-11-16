import { Navigate, Route, Routes, useLocation } from "react-router-dom";

import { useAuth } from "./auth/session";
import { Logo } from "./components/Logo";
import { AppLayout } from "./layout/AppLayout";
import { ChooseBusiness } from "./pages/ChooseBusiness";
import { ComingSoon } from "./pages/ComingSoon";
import { Home } from "./pages/Home";
import { Login } from "./pages/Login";

const SOON = [
  { path: "bandeja", title: "Bandeja", block: "6.3", text: "Todas las conversaciones de WhatsApp, al instante." },
  { path: "numeros", title: "Números de WhatsApp", block: "6.2", text: "Conecta tus números con un QR y mira su estado." },
  { path: "agente", title: "Agente y horario", block: "6.4", text: "Lo que dice tu agente y cuándo atiende tu negocio." },
  { path: "conocimiento", title: "Conocimiento", block: "6.5", text: "Lo que el bot sabe de tu negocio." },
  { path: "equipo", title: "Equipo", block: "6.7", text: "Quién atiende contigo y qué puede hacer cada uno." },
  { path: "consumo", title: "Consumo", block: "6.6", text: "Cuántos mensajes y respuestas de IA usa tu negocio." },
  { path: "ajustes", title: "Ajustes del negocio", block: "6.7", text: "Nombre, país y zona horaria del negocio." },
];

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
        {SOON.map((s) => (
          <Route key={s.path} path={s.path} element={<ComingSoon title={s.title} block={s.block} text={s.text} />} />
        ))}
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
