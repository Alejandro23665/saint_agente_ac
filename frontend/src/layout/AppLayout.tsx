import { useEffect, useRef, useState } from "react";
import { NavLink, Outlet } from "react-router-dom";

import { initials, ROLE_NAMES, useAuth } from "../auth/session";
import { Icon } from "../components/Icon";
import type { IconName } from "../components/Icon";
import { Logo } from "../components/Logo";
import { DashboardProvider, useDashboard, useInboxCounts } from "./dashboard";

interface Section {
  to: string;
  label: string;
  short: string;
  icon: IconName;
}

export const SECTIONS: Section[] = [
  { to: "/", label: "Inicio", short: "Inicio", icon: "home" },
  { to: "/bandeja", label: "Bandeja", short: "Bandeja", icon: "chat" },
  { to: "/numeros", label: "Números de WhatsApp", short: "Números", icon: "phone" },
  { to: "/agente", label: "Agente y horario", short: "Agente", icon: "bot" },
  { to: "/conocimiento", label: "Conocimiento", short: "Conocimiento", icon: "book" },
  { to: "/equipo", label: "Equipo", short: "Equipo", icon: "team" },
  { to: "/consumo", label: "Consumo", short: "Consumo", icon: "chart" },
];
export const SETTINGS: Section = { to: "/ajustes", label: "Ajustes del negocio", short: "Ajustes", icon: "settings" };

function navClass({ isActive }: { isActive: boolean }) {
  return isActive ? "nav-link active" : "nav-link";
}

function WaitingBadge() {
  const { session } = useAuth();
  const counts = useInboxCounts(session?.user.id ?? "");
  if (!counts?.waiting) return null;
  return (
    <span className="badge" aria-label={`${counts.waiting} esperan respuesta`}>
      {counts.waiting}
    </span>
  );
}

function BusinessSwitcher() {
  const { session, selectTenant } = useAuth();
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent | KeyboardEvent) => {
      if (event instanceof KeyboardEvent ? event.key === "Escape" : !box.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", close);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", close);
    };
  }, [open]);

  if (!session?.active_tenant) return null;
  const active = session.active_tenant;
  const several = session.tenants.length > 1;

  return (
    <div className="switcher" ref={box}>
      <button
        type="button"
        className="switcher-button"
        aria-haspopup={several ? "true" : undefined}
        aria-expanded={several ? open : undefined}
        aria-label={several ? `Negocio: ${active.name}. Cambiar de negocio` : `Negocio: ${active.name}`}
        onClick={() => several && setOpen((v) => !v)}
        disabled={!several}
      >
        <span className="avatar avatar-sm">{initials(active.name)}</span>
        <span>{active.name}</span>
        {several && <Icon name="chevronDown" size={18} />}
      </button>
      {open && (
        <ul className="switcher-menu">
          {session.tenants.map((tenant) => (
            <li key={tenant.id}>
              <button
                type="button"
                aria-current={tenant.id === active.id ? "true" : undefined}
                onClick={() => {
                  setOpen(false);
                  if (tenant.id !== active.id) void selectTenant(tenant.id);
                }}
              >
                <span className="avatar avatar-sm">{initials(tenant.name)}</span>
                <span>
                  <strong>{tenant.name}</strong>
                  <small>{ROLE_NAMES[tenant.role] ?? tenant.role}</small>
                </span>
                {tenant.id === active.id && <Icon name="check" size={16} />}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function StatusPills() {
  const { numbers, hours } = useDashboard();
  const connected = numbers?.some((n) => n.status === "open") ?? false;
  return (
    <div className="topbar-status">
      {numbers && (
        <span className={connected ? "pill pill-ice" : "pill pill-muted"}>
          <span className={connected ? "dot" : "dot dot-off"} />
          {connected ? "Agente IA activo" : "Agente IA sin número"}
        </span>
      )}
      {hours && (
        <span className={hours.open_now ? "pill pill-success pill-open" : "pill pill-warn pill-open"}>
          {hours.open_now ? "Abierto ahora" : "Fuera de horario"}
        </span>
      )}
    </div>
  );
}

function Shell() {
  const { session, logout } = useAuth();
  const [moreOpen, setMoreOpen] = useState(false);
  if (!session?.active_tenant) return null;
  const role = ROLE_NAMES[session.active_tenant.role] ?? session.active_tenant.role;
  const mobileMain = SECTIONS.slice(0, 4);
  const mobileMore = [...SECTIONS.slice(4), SETTINGS];

  return (
    <div className="shell">
      <nav className="sidebar" aria-label="Menú del panel">
        <Logo width={128} />
        <ul className="nav">
          {SECTIONS.map((s) => (
            <li key={s.to}>
              <NavLink to={s.to} end={s.to === "/"} className={navClass}>
                <Icon name={s.icon} />
                <span>{s.label}</span>
                {s.to === "/bandeja" && <WaitingBadge />}
              </NavLink>
            </li>
          ))}
        </ul>
        <div className="sidebar-foot">
          <NavLink to={SETTINGS.to} className={navClass}>
            <Icon name={SETTINGS.icon} />
            <span>{SETTINGS.label}</span>
          </NavLink>
          <div className="me">
            <span className="avatar">{initials(session.user.full_name)}</span>
            <span>
              <strong>{session.user.full_name}</strong>
              <small>{role}</small>
            </span>
            <button type="button" className="btn-icon" aria-label="Cerrar sesión" title="Cerrar sesión" onClick={() => void logout()}>
              <Icon name="logout" />
            </button>
          </div>
        </div>
      </nav>

      <div className="content">
        <header className="topbar">
          <span className="topbar-logo">
            <Logo width={96} />
          </span>
          <StatusPills />
          <BusinessSwitcher />
        </header>
        <Outlet />
      </div>

      <nav className="bottom-nav" aria-label="Menú del panel">
        {mobileMain.map((s) => (
          <NavLink key={s.to} to={s.to} end={s.to === "/"} className={({ isActive }) => (isActive ? "active" : "")} onClick={() => setMoreOpen(false)}>
            <Icon name={s.icon} size={22} />
            {s.short}
          </NavLink>
        ))}
        <button type="button" aria-expanded={moreOpen} aria-controls="mas" onClick={() => setMoreOpen((v) => !v)}>
          <Icon name="more" size={22} />
          Más
        </button>
      </nav>
      <div id="mas" className="more-sheet" data-open={moreOpen}>
        {mobileMore.map((s) => (
          <NavLink key={s.to} to={s.to} className={navClass} onClick={() => setMoreOpen(false)}>
            <Icon name={s.icon} />
            <span>{s.label}</span>
          </NavLink>
        ))}
        <button type="button" className="nav-link link-button" onClick={() => void logout()}>
          <Icon name="logout" />
          <span>Cerrar sesión ({session.user.full_name})</span>
        </button>
      </div>
    </div>
  );
}

export function AppLayout() {
  const { session } = useAuth();
  if (!session?.active_tenant) return null;
  return (
    <DashboardProvider tenantId={session.active_tenant.id}>
      <Shell />
    </DashboardProvider>
  );
}
