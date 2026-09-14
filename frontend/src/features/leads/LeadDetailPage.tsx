import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { apiFetch } from "../../lib/api";

interface Lead {
  id: string;
  profile: string;
  validation_status: string;
  score: number | null;
  score_reason: string | null;
  priority: number;
  next_follow_up_at: string | null;
  setter_name: string | null;
  closer_name: string | null;
  pipeline_stage_name: string | null;
  giro: string | null;
  zona: string | null;
  web_status: string | null;
  web_check_detail: string | null;
  google_presence: string | null;
  meta_ads_active: boolean | null;
  contacted: boolean;
  source_searched_at: string | null;
  outreach_message: string | null;
  business: { name: string; address: string | null; phone: string | null; website: string | null; rating: number | null; reviews_total: number | null };
}

interface Activity { id: string; type: string; content: string; created_at: string }

export function LeadDetailPage() {
  const { leadId } = useParams<{ leadId: string }>();
  const [lead, setLead] = useState<Lead | null>(null);
  const [timeline, setTimeline] = useState<Activity[]>([]);
  const [comment, setComment] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    if (!leadId) return;
    try {
      const [leadData, timelineData] = await Promise.all([
        apiFetch<Lead>(`/leads/${leadId}`),
        apiFetch<{ items: Activity[] }>(`/activities/leads/${leadId}/timeline`),
      ]);
      setLead(leadData);
      setTimeline(timelineData.items);
    } catch (err) { setError(err instanceof Error ? err.message : "No se pudo cargar el lead"); }
  }, [leadId]);

  useEffect(() => { void load(); }, [load]);

  const addComment = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!leadId || !comment.trim()) return;
    try {
      await apiFetch("/activities", { method: "POST", body: JSON.stringify({ lead_id: leadId, type: "note", content: comment.trim() }) });
      setComment("");
      await load();
    } catch (err) { setError(err instanceof Error ? err.message : "No se pudo guardar el comentario"); }
  };

  if (!lead) return <div className="card">{error || "Cargando lead…"}</div>;
  return (
    <div className="max-w-5xl mx-auto space-y-6">
      <div><Link className="text-sm text-blue-700" to="/leads">← Volver a leads</Link><h2 className="mt-2 text-2xl font-bold text-gray-900">{lead.business.name}</h2><p className="text-gray-600">{lead.business.address || "Sin dirección"}</p></div>
      {error && <p role="alert" className="text-red-600">{error}</p>}
      <div className="grid gap-6 lg:grid-cols-[1fr_1.4fr]">
        <section className="card space-y-4"><div className="flex flex-wrap gap-2"><span className="badge">{lead.profile}</span><span className="badge">{lead.pipeline_stage_name || "Nuevo"}</span></div><Info label="Teléfono" value={lead.business.phone || "No disponible"} /><Info label="Sitio web" value={lead.business.website || "No disponible"} /><Info label="Validación" value={lead.validation_status} /><Info label="Setter" value={lead.setter_name || "Sin asignar"} /><Info label="Closer" value={lead.closer_name || "Sin asignar"} /><Info label="Puntuación" value={lead.score === null ? "Pendiente" : `${lead.score}/100`} />{lead.score_reason && <p className="text-sm text-gray-600">{lead.score_reason}</p>}<div className="border-t border-slate-100 pt-4"><p className="eyebrow">Datos importados</p><div className="mt-3 grid gap-3 sm:grid-cols-2"><Info label="Giro" value={lead.giro || "No disponible"} /><Info label="Zona" value={lead.zona || "No disponible"} /><Info label="Estado web" value={lead.web_status || "No validado"} /><Info label="Presencia Google" value={lead.google_presence || "No validado"} /><Info label="Meta Ads" value={lead.meta_ads_active === null ? "Sin dato" : lead.meta_ads_active ? "Activos" : "No detectados"} /><Info label="Contacto" value={lead.contacted ? "Contactado" : "Pendiente"} /></div></div>{lead.outreach_message && <div className="border-t border-slate-100 pt-4"><p className="eyebrow">Mensaje frío</p><p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-slate-700">{lead.outreach_message}</p></div>}</section>
        <section className="card"><h3 className="font-semibold text-gray-900">Línea de tiempo</h3><form onSubmit={addComment} className="mt-4 flex gap-2"><input aria-label="Nuevo comentario" className="input flex-1" value={comment} onChange={(event) => setComment(event.target.value)} placeholder="Agregar comentario o nota" /><button className="btn-primary" type="submit">Guardar</button></form><ol className="mt-6 space-y-4">{timeline.map((item) => <li key={item.id} className="border-l-2 border-gray-200 pl-3"><p className="text-xs uppercase tracking-wide text-gray-500">{item.type}</p><p className="text-sm text-gray-800">{item.content}</p><time className="text-xs text-gray-500">{new Date(item.created_at).toLocaleString("es-MX")}</time></li>)}{timeline.length === 0 && <li className="text-sm text-gray-500">Todavía no hay actividad registrada.</li>}</ol></section>
      </div>
    </div>
  );
}

function Info({ label, value }: { label: string; value: string }) { return <div><p className="text-xs uppercase tracking-wide text-gray-500">{label}</p><p className="text-gray-900">{value}</p></div>; }
