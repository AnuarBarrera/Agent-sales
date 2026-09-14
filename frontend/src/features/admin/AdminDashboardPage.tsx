import { useEffect, useState } from "react";
import { getSummary, Summary } from "./adminApi";

export function AdminDashboardPage() {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => { getSummary().then(setSummary).catch((reason: Error) => setError(reason.message)); }, []);

  if (error) return <div className="card"><h1 className="section-title">No se pudo cargar el resumen</h1><p className="mt-2 text-sm text-red-600">{error}</p></div>;
  if (!summary) return <p className="text-sm text-slate-500">Cargando resumen…</p>;

  const metrics = [
    ["Leads sin responsable", summary.unassigned_leads, "text-amber-600"],
    ["Tareas vencidas", summary.overdue_tasks, "text-red-600"],
    ["Seguimientos de hoy", summary.due_today, "text-teal-700"],
  ] as const;
  return <div className="space-y-8">
    <div><p className="eyebrow">Vista general</p><h1 className="page-title">Administración</h1><p className="page-subtitle">El pulso del workspace y la carga de cada persona, en un solo lugar.</p></div>
    <section className="grid gap-4 sm:grid-cols-3">{metrics.map(([label, value, color]) => <div className="card" key={label}><p className="text-sm font-semibold text-slate-500">{label}</p><p className={`mt-3 text-4xl font-black ${color}`}>{value}</p></div>)}</section>
    <section className="grid gap-6 lg:grid-cols-[1.1fr_.9fr]">
      <div className="card"><h2 className="section-title">Pipeline</h2><div className="mt-5 space-y-4">{summary.stages.length === 0 ? <p className="text-sm text-slate-500">Aún no hay etapas con leads.</p> : summary.stages.map(stage => <div key={stage.id}><div className="flex justify-between text-sm"><span className="font-medium">{stage.label}</span><span className="font-bold text-teal-700">{stage.lead_count}</span></div><div className="mt-2 h-2 rounded-full bg-slate-100"><div className="h-2 rounded-full bg-teal-500" style={{ width: `${Math.min(stage.lead_count * 10, 100)}%` }} /></div></div>)}</div></div>
      <div className="card"><h2 className="section-title">Carga por miembro</h2><div className="mt-4 divide-y divide-slate-100">{summary.member_workload.length === 0 ? <p className="text-sm text-slate-500">No hay miembros activos.</p> : summary.member_workload.map(member => <div className="flex items-center justify-between py-3" key={member.id}><div><p className="font-semibold">{member.full_name}</p><p className="text-xs capitalize text-slate-400">{member.role}</p></div><span className="badge">{member.lead_count} leads</span></div>)}</div></div>
    </section>
  </div>;
}
