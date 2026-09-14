import { FormEvent, useEffect, useState } from "react";
import { getMembers, inviteMember, Member, Invitation, Role, updateMember } from "./adminApi";

const roleLabels: Record<Role, string> = { admin: "Admin", setter: "Setter", closer: "Closer" };

export function TeamPage() {
  const [members, setMembers] = useState<Member[]>([]);
  const [invitations, setInvitations] = useState<Invitation[]>([]);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<"setter" | "closer">("setter");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => { getMembers().then(setMembers).catch((reason: Error) => setError(reason.message)); }, []);
  async function submit(event: FormEvent) { event.preventDefault(); setError(null); try { const invitation = await inviteMember(email, role); setInvitations(current => [invitation, ...current]); setEmail(""); } catch (reason) { setError((reason as Error).message); } }
  async function changeMember(member: Member, update: { role?: Role; status?: "active" | "inactive" }) { try { const updated = await updateMember(member.user_id, update); setMembers(current => current.map(item => item.id === updated.id ? updated : item)); } catch (reason) { setError((reason as Error).message); } }

  return <div className="space-y-8">
    <div><p className="eyebrow">Workspace</p><h1 className="page-title">Equipo</h1><p className="page-subtitle">Invita personas y mantén claros sus permisos y acceso.</p></div>
    {error && <div role="alert" className="rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>}
    <section className="card"><div className="flex flex-wrap items-start justify-between gap-4"><div><h2 className="section-title">Nueva invitación</h2><p className="mt-1 text-sm text-slate-500">Copia el enlace generado y compártelo por tu canal habitual.</p></div></div><form onSubmit={submit} className="mt-5 grid gap-3 sm:grid-cols-[1fr_10rem_auto]"><label className="sr-only" htmlFor="invite-email">Correo</label><input id="invite-email" className="input" type="email" required value={email} onChange={event => setEmail(event.target.value)} placeholder="persona@empresa.com" /><label className="sr-only" htmlFor="invite-role">Rol</label><select id="invite-role" className="input" value={role} onChange={event => setRole(event.target.value as "setter" | "closer")}><option value="setter">Setter</option><option value="closer">Closer</option></select><button className="btn-primary" type="submit">Crear invitación</button></form>{invitations.length > 0 && <div className="mt-5 space-y-2">{invitations.map(invitation => <div className="rounded-xl bg-teal-50 px-4 py-3 text-sm text-teal-900" key={invitation.id}>Invitación para <strong>{invitation.email}</strong> · {roleLabels[invitation.role]}</div>)}</div>}</section>
    <section className="card overflow-x-auto"><h2 className="section-title">Miembros</h2><table className="mt-5 w-full min-w-[42rem] text-left text-sm"><thead className="border-b border-slate-200 text-xs font-bold uppercase tracking-wide text-slate-400"><tr><th className="pb-3">Persona</th><th className="pb-3">Rol</th><th className="pb-3">Estado</th><th className="pb-3 text-right">Acciones</th></tr></thead><tbody className="divide-y divide-slate-100">{members.map(member => <tr key={member.id}><td className="py-4"><p className="font-semibold">{member.user.full_name}</p><p className="text-xs text-slate-400">{member.user.email}</p></td><td className="py-4"><select aria-label={`Rol de ${member.user.full_name}`} className="input py-2" value={member.role} onChange={event => changeMember(member, { role: event.target.value as Role })}><option value="admin">Admin</option><option value="setter">Setter</option><option value="closer">Closer</option></select></td><td className="py-4"><span className={`status ${member.status === "active" ? "bg-emerald-100 text-emerald-700" : "bg-slate-100 text-slate-500"}`}>{member.status === "active" ? "Activo" : "Inactivo"}</span></td><td className="py-4 text-right"><button className="btn-secondary py-2" onClick={() => changeMember(member, { status: member.status === "active" ? "inactive" : "active" })}>{member.status === "active" ? "Desactivar" : "Activar"}</button></td></tr>)}</tbody></table>{members.length === 0 && !error && <p className="mt-5 text-sm text-slate-500">No hay miembros para mostrar.</p>}</section>
  </div>;
}
