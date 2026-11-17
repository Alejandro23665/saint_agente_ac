import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { refreshSession } from "../api/client";
import { teamApi } from "../api/team";
import type { Invitation, Member, Role } from "../api/types";
import { initials, useAuth, useRole } from "../auth/session";
import { Alert } from "../components/Alert";
import { Button } from "../components/Button";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { EmptyState, Spinner, StatusPill } from "../components/Feedback";
import { InviteDialog } from "../components/InviteDialog";
import { useToast } from "../components/Toast";
import { useDashboardRefresh } from "../layout/dashboard";
import { dateTime, plural, roleLabel, shortDate, timeAgo } from "../lib/format";
import { canChangeRoles, canManage, needsConfirmation, ownerCount, ROLE_MATRIX } from "../lib/permissions";
import { errorText, useResource } from "../lib/useResource";

type Dialog =
  | { kind: "invite" }
  | { kind: "role"; member: Member; role: Role }
  | { kind: "remove"; member: Member }
  | { kind: "leave" }
  | { kind: "revoke"; invitation: Invitation }
  | null;

const ROLES: Role[] = ["owner", "admin", "agent"];

export function Team() {
  const { session } = useAuth();
  const { role: myRole, canEdit } = useRole();
  const toast = useToast();
  const refreshDashboard = useDashboardRefresh();
  const navigate = useNavigate();
  const members = useResource(teamApi.members);
  // Solo el dueño y los administradores pueden ver las invitaciones.
  const invitations = useResource(() => (canEdit ? teamApi.invitations() : Promise.resolve([] as Invitation[])), [canEdit]);
  const [dialog, setDialog] = useState<Dialog>(null);
  const [changing, setChanging] = useState<string | null>(null);

  const list = members.data ?? [];
  const owners = ownerCount(list);
  const me = list.find((m) => m.is_me);
  const onlyOwner = me?.role === "owner" && owners <= 1;

  function changed() {
    void members.reload();
    refreshDashboard(["members"]);
  }

  async function applyRole(member: Member, role: Role) {
    await teamApi.changeRole(member.user_id, role);
    toast.success(`${member.full_name} ahora es ${roleLabel(role).toLowerCase()}`);
    changed();
  }

  async function pickRole(member: Member, role: Role) {
    if (role === member.role) return;
    if (needsConfirmation(member.role, role)) {
      setDialog({ kind: "role", member, role });
      return;
    }
    setChanging(member.user_id);
    try {
      await applyRole(member, role);
    } catch (e) {
      toast.error(errorText(e));
    } finally {
      setChanging(null);
    }
  }

  return (
    <main className="page">
      <div className="page-head page-head-row">
        <div>
          <h1>Equipo</h1>
          <p>Quién atiende contigo y qué puede hacer cada uno.</p>
        </div>
        {canEdit && (
          <Button icon="plus" onClick={() => setDialog({ kind: "invite" })}>
            Invitar a alguien
          </Button>
        )}
      </div>

      {members.error && (
        <Alert kind="error" title="No pudimos cargar el equipo">
          {members.error}{" "}
          <button type="button" className="link-button" onClick={() => void members.reload()}>
            Reintentar
          </button>
        </Alert>
      )}

      <section className="card" aria-labelledby="miembros-titulo">
        <div className="card-head">
          <h2 id="miembros-titulo">Miembros</h2>
          {members.data && <small>{plural(list.length, "persona")}</small>}
        </div>
        {members.loading ? (
          <Spinner label="Cargando el equipo" />
        ) : (
          <ul className="people">
            {list.map((m) => {
              const manageable = !m.is_me && canManage(myRole, m.role);
              return (
                <li key={m.user_id} className="person">
                  <span className="avatar">{initials(m.full_name)}</span>
                  <div className="person-main">
                    <strong>
                      {m.full_name}
                      {m.is_me && <span className="you"> (tú)</span>}
                    </strong>
                    <small>{m.email}</small>
                    <small>
                      Desde el {shortDate(m.joined_at)} · {m.last_login_at ? `última entrada ${timeAgo(m.last_login_at)}` : "todavía no ha entrado"}
                    </small>
                  </div>
                  <div className="person-actions">
                    {manageable && canChangeRoles(myRole) ? (
                      <select
                        className="input select input-inline"
                        aria-label={`Rol de ${m.full_name}`}
                        value={m.role}
                        disabled={changing === m.user_id}
                        onChange={(e) => void pickRole(m, e.target.value as Role)}
                      >
                        {ROLES.map((r) => (
                          <option key={r} value={r}>
                            {roleLabel(r)}
                          </option>
                        ))}
                      </select>
                    ) : (
                      <StatusPill tone={m.role === "agent" ? "muted" : "ice"}>{roleLabel(m.role)}</StatusPill>
                    )}
                    {manageable && (
                      <Button
                        variant="ghost"
                        small
                        className="danger-text"
                        onClick={() => setDialog({ kind: "remove", member: m })}
                        aria-label={`Quitar a ${m.full_name}`}
                      >
                        Quitar
                      </Button>
                    )}
                    {m.is_me && (
                      <Button
                        variant="ghost"
                        small
                        className="danger-text"
                        onClick={() => setDialog({ kind: "leave" })}
                        disabled={onlyOwner}
                        title={onlyOwner ? "Eres el único dueño: nombra a otro dueño primero" : undefined}
                      >
                        Salir del negocio
                      </Button>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        )}
        {onlyOwner && (
          <p className="muted">
            Eres el único dueño. Para poder salir, primero nombra a otro dueño (cambia su rol en esta lista). El negocio siempre conserva al menos un dueño.
          </p>
        )}
      </section>

      {canEdit && (
        <section className="card" aria-labelledby="invitaciones-titulo">
          <div className="card-head">
            <h2 id="invitaciones-titulo">Invitaciones pendientes</h2>
            {invitations.data && invitations.data.length > 0 && <small>{invitations.data.length}</small>}
          </div>
          {invitations.error && (
            <Alert kind="error" title="No pudimos cargar las invitaciones">
              {invitations.error}
            </Alert>
          )}
          {invitations.loading ? (
            <Spinner label="Cargando las invitaciones" />
          ) : !invitations.data?.length ? (
            <EmptyState icon="mail" title="No hay invitaciones pendientes">
              Cuando invites a alguien, aparecerá aquí hasta que acepte o venza el enlace (72 horas).
            </EmptyState>
          ) : (
            <ul className="people">
              {invitations.data.map((inv) => (
                <li key={inv.id} className="person">
                  <span className="avatar">
                    <span aria-hidden="true">@</span>
                  </span>
                  <div className="person-main">
                    <strong>{inv.email}</strong>
                    <small>
                      {roleLabel(inv.role)} · {inv.expired ? `venció el ${dateTime(inv.expires_at)}` : `vence el ${dateTime(inv.expires_at)}`}
                    </small>
                  </div>
                  <div className="person-actions">
                    {inv.expired && <StatusPill tone="warn">Venció</StatusPill>}
                    <Button
                      variant="ghost"
                      small
                      className="danger-text"
                      onClick={() => setDialog({ kind: "revoke", invitation: inv })}
                      aria-label={`Anular la invitación de ${inv.email}`}
                    >
                      Anular
                    </Button>
                  </div>
                </li>
              ))}
            </ul>
          )}
          <p className="muted">El enlace de una invitación solo se muestra al crearla. Si se perdió, vuelve a invitar al mismo correo.</p>
        </section>
      )}

      <section className="card" aria-labelledby="roles-titulo">
        <h2 id="roles-titulo">Qué puede hacer cada rol</h2>
        <div className="table-wrap">
          <table className="matrix">
            <thead>
              <tr>
                <th scope="col">Acción</th>
                <th scope="col">Dueño</th>
                <th scope="col">Administrador</th>
                <th scope="col">Agente</th>
              </tr>
            </thead>
            <tbody>
              {ROLE_MATRIX.map((row) => (
                <tr key={row.action}>
                  <th scope="row">{row.action}</th>
                  <td>{row.owner}</td>
                  <td>{row.admin}</td>
                  <td>{row.agent}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="muted">Nadie cambia su propio rol, y el negocio siempre conserva al menos un dueño. Si quitas a alguien, deja de entrar al momento.</p>
      </section>

      {dialog?.kind === "invite" && myRole && (
        <InviteDialog
          myRole={myRole}
          onClose={() => setDialog(null)}
          onInvited={() => {
            void invitations.reload();
          }}
        />
      )}

      {dialog?.kind === "role" && (
        <ConfirmDialog
          title="Cambiar el rol"
          confirmLabel="Cambiar rol"
          danger={dialog.role !== "owner"}
          onClose={() => setDialog(null)}
          onConfirm={() => applyRole(dialog.member, dialog.role)}
        >
          {dialog.role === "owner"
            ? `${dialog.member.full_name} pasará a ser dueño: tendrá control total del negocio, incluido quitarte a ti.`
            : `${dialog.member.full_name} dejará de ser dueño y pasará a ser ${roleLabel(dialog.role).toLowerCase()}.`}
        </ConfirmDialog>
      )}

      {dialog?.kind === "remove" && (
        <ConfirmDialog
          title="Quitar del equipo"
          confirmLabel="Quitar"
          danger
          onClose={() => setDialog(null)}
          onConfirm={async () => {
            await teamApi.removeMember(dialog.member.user_id);
            toast.success(`${dialog.member.full_name} ya no tiene acceso`);
            changed();
          }}
        >
          {dialog.member.full_name} dejará de poder entrar a este negocio en este mismo momento. Para devolverle el acceso, habrá que invitarle de nuevo.
        </ConfirmDialog>
      )}

      {dialog?.kind === "leave" && (
        <ConfirmDialog
          title="Salir del negocio"
          confirmLabel="Salir del negocio"
          danger
          onClose={() => setDialog(null)}
          onConfirm={async () => {
            await teamApi.removeMember(session?.user.id ?? "");
            toast.success("Saliste del negocio");
            // La lista de negocios cambió: se renueva la sesión y el panel te lleva a elegir otro.
            await refreshSession(null);
            navigate("/", { replace: true });
          }}
        >
          Dejarás de tener acceso a este negocio. Para volver, alguien tendrá que invitarte otra vez.
        </ConfirmDialog>
      )}

      {dialog?.kind === "revoke" && (
        <ConfirmDialog
          title="Anular la invitación"
          confirmLabel="Anular invitación"
          danger
          onClose={() => setDialog(null)}
          onConfirm={async () => {
            await teamApi.revokeInvitation(dialog.invitation.id);
            toast.success("Invitación anulada");
            void invitations.reload();
          }}
        >
          El enlace enviado a {dialog.invitation.email} dejará de funcionar.
        </ConfirmDialog>
      )}
    </main>
  );
}
