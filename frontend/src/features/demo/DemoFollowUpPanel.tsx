import { useEffect, useRef, useState, type FormEvent } from "react";
import { apiFetch } from "../../lib/api";

interface FollowUpActivity { id: string; type: string; content: string; created_at: string }
interface FollowUpTask { id: string; title: string; due_at: string | null; status: string }
interface FollowUpRead {
  lead_id: string; contacted: boolean; first_contacted_at: string | null;
  next_follow_up_at: string | null; activities: FollowUpActivity[]; tasks: FollowUpTask[];
}
const outcomeLabels: Record<string, string> = {
  no_response: "Sin respuesta", conversation: "En conversación", appointment: "Cita agendada", not_interested: "No interesado",
};
const formatDate = (value: string) => new Date(value).toLocaleString("es-MX", { dateStyle: "medium", timeStyle: "short" });

export function DemoFollowUpPanel({ leadId, onContactRecorded }: { leadId: string; onContactRecorded: () => void }) {
  const [data, setData] = useState<FollowUpRead | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [busy, setBusy] = useState("");
  const [note, setNote] = useState("");
  const [channel, setChannel] = useState("phone");
  const [outcome, setOutcome] = useState("no_response");
  const [contactNote, setContactNote] = useState("");
  const [title, setTitle] = useState("");
  const [dueAt, setDueAt] = useState("");
  const active = useRef(false);
  const inFlight = useRef(false);
  const anchorShown = useRef(false);
  const intents = useRef<Record<string, { content: string; id: string }>>({});
  const endpoint = `/demo/leads/${encodeURIComponent(leadId)}/follow-up`;

  useEffect(() => {
    active.current = true;
    setLoading(true); setError("");
    void apiFetch<FollowUpRead>(endpoint).then((next) => { if (active.current) setData(next); })
      .catch((reason: unknown) => { if (active.current) setError(reason instanceof Error ? reason.message : "No se pudo cargar el seguimiento."); })
      .finally(() => { if (active.current) setLoading(false); });
    return () => { active.current = false; };
  }, [endpoint]);

  useEffect(() => {
    if (data && !anchorShown.current && window.location.hash === "#lead-follow-up") {
      document.getElementById("lead-follow-up")?.scrollIntoView({ block: "start" });
      anchorShown.current = true;
    }
  }, [data]);

  const save = async (action: string, payload: Record<string, string>, confirmation: string) => {
    if (inFlight.current) return false;
    inFlight.current = true; setBusy(action); setError(""); setSuccess("");
    const content = JSON.stringify(payload);
    if (intents.current[action]?.content !== content) intents.current[action] = { content, id: crypto.randomUUID() };
    try {
      const next = await apiFetch<FollowUpRead>(`${endpoint}/${action}`, {
        method: "POST", body: JSON.stringify({ ...payload, request_id: intents.current[action].id }),
      });
      if (!active.current) return false;
      setData(next); setSuccess(confirmation); delete intents.current[action];
      if (action === "contacts") onContactRecorded();
      return true;
    } catch (reason) {
      if (active.current) setError(reason instanceof Error ? reason.message : "No se pudo guardar el seguimiento.");
      return false;
    } finally { inFlight.current = false; if (active.current) setBusy(""); }
  };

  const addNote = async (event: FormEvent) => {
    event.preventDefault();
    if (note.trim() && await save("notes", { content: note.trim() }, "Nota guardada.")) setNote("");
  };
  const recordContact = async (event: FormEvent) => {
    event.preventDefault();
    if (await save("contacts", { channel, outcome, note: contactNote.trim() }, "Contacto y resultado registrados.")) setContactNote("");
  };
  const schedule = async (event: FormEvent) => {
    event.preventDefault();
    const date = new Date(dueAt);
    if (!title.trim() || !Number.isFinite(date.getTime()) || date.getTime() <= Date.now()) {
      setError("Escribe una tarea y elige una fecha futura para el seguimiento."); return;
    }
    if (await save("tasks", { title: title.trim(), due_at: date.toISOString() }, "Seguimiento programado.")) { setTitle(""); setDueAt(""); }
  };

  return <section id="lead-follow-up" className="card space-y-5" aria-labelledby="follow-up-heading">
    <div><p className="eyebrow">Actividad comercial</p><h3 id="follow-up-heading" className="mt-1 text-lg font-bold text-slate-900">Seguimiento del lead</h3><p className="mt-1 text-sm text-slate-600">Registra lo que ocurrió y organiza tu próxima acción.</p></div>
    {loading && <p role="status">Consultando seguimiento…</p>}
    {error && <div className="space-y-2"><p role="alert" className="text-sm text-red-700">{error}</p>{!data && <button className="btn-secondary" onClick={() => window.location.reload()}>Reintentar carga</button>}</div>}
    {success && <p role="status" className="text-sm text-teal-800">{success}</p>}
    {data && <>
      <div className="grid gap-3 rounded-xl bg-slate-50 p-3 text-sm sm:grid-cols-2">
        <div><p className="font-semibold text-slate-800">Primer contacto</p><p className="text-slate-600">{data.first_contacted_at ? formatDate(data.first_contacted_at) : "Sin contacto registrado"}</p></div>
        <div><p className="font-semibold text-slate-800">Próximo seguimiento</p><p className={data.next_follow_up_at && new Date(data.next_follow_up_at).getTime() < Date.now() ? "text-amber-800" : "text-slate-600"}>{data.next_follow_up_at ? formatDate(data.next_follow_up_at) : "Sin fecha programada"}</p></div>
      </div>
      <details className="rounded-xl border border-slate-200 p-3">
        <summary className="cursor-pointer font-semibold text-slate-900">Registrar contacto realizado</summary>
        <form onSubmit={(event) => void recordContact(event)} className="mt-3 space-y-3">
          <p className="text-xs text-slate-500">Registra un correo o una llamada que ya realizaste por tu cuenta.</p>
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="text-sm">Canal del contacto<select disabled={!!busy} className="input mt-1 w-full" value={channel} onChange={(event) => setChannel(event.target.value)}><option value="phone">Llamada</option><option value="email">Correo</option></select></label>
            <label className="text-sm">Resultado<select disabled={!!busy} className="input mt-1 w-full" value={outcome} onChange={(event) => setOutcome(event.target.value)}>{Object.entries(outcomeLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
          </div>
          <label className="block text-sm">Detalle del contacto<textarea disabled={!!busy} className="input mt-1 w-full" maxLength={4000} value={contactNote} onChange={(event) => setContactNote(event.target.value)} placeholder="Con quién hablaste o qué ocurrió" /></label>
          <button className="btn-primary" disabled={!!busy}>{busy === "contacts" ? "Guardando…" : "Guardar contacto realizado"}</button>
        </form>
      </details>
      <form onSubmit={(event) => void addNote(event)} className="space-y-2">
        <label className="block text-sm font-semibold text-slate-800">Nota de seguimiento<textarea disabled={!!busy} className="input mt-1 w-full font-normal" value={note} onChange={(event) => setNote(event.target.value)} maxLength={4000} required placeholder="Información útil para retomar la conversación" /></label>
        <button className="btn-secondary" disabled={!!busy || !note.trim()}>{busy === "notes" ? "Guardando…" : "Guardar nota"}</button>
      </form>
      <form onSubmit={(event) => void schedule(event)} className="space-y-3 border-t border-slate-100 pt-4">
        <h4 className="font-semibold text-slate-900">Programar seguimiento</h4>
        <label className="block text-sm">Próxima acción<input disabled={!!busy} className="input mt-1 w-full" value={title} onChange={(event) => setTitle(event.target.value)} maxLength={255} required placeholder="Por ejemplo: volver a llamar" /></label>
        <label className="block min-w-0 text-sm">Fecha y hora local<input disabled={!!busy} className="input mt-1 w-full min-w-0" type="datetime-local" value={dueAt} onChange={(event) => setDueAt(event.target.value)} required /></label>
        <button className="btn-secondary" disabled={!!busy || !title.trim() || !dueAt}>{busy === "tasks" ? "Programando…" : "Programar seguimiento"}</button>
      </form>
      <div className="border-t border-slate-100 pt-4"><h4 className="font-semibold text-slate-900">Seguimientos pendientes</h4>
        <ul className="mt-3 space-y-3">{data.tasks.map((task) => <li key={task.id} className="rounded-lg border border-slate-200 p-3">
          <p className="font-medium text-slate-800">{task.title}</p>
          {task.due_at && <p className="mt-1 text-xs text-slate-500">{new Date(task.due_at).getTime() < Date.now() ? "Vencido · " : ""}{formatDate(task.due_at)}</p>}
          <button className="btn-secondary mt-2" type="button" disabled={!!busy} aria-label={`Completar seguimiento: ${task.title}`} onClick={() => void save(`tasks/${task.id}/complete`, {}, "Seguimiento completado.")}>Marcar completado</button>
        </li>)}</ul>{!data.tasks.length && <p className="mt-2 text-sm text-slate-500">No hay seguimientos pendientes.</p>}
      </div>
      <div className="border-t border-slate-100 pt-4"><h4 className="font-semibold text-slate-900">Historial del lead</h4>
        <p className="mt-1 text-xs text-slate-500">Últimas 100 actividades registradas.</p>
        <ol className="mt-3 space-y-4">{data.activities.map((activity) => <li key={activity.id} className="border-l-2 border-teal-200 pl-3"><p className="whitespace-pre-wrap break-words text-sm text-slate-800">{activityLabel(activity)}</p><time className="text-xs text-slate-500" dateTime={activity.created_at}>{formatDate(activity.created_at)}</time></li>)}</ol>
        {!data.activities.length && <p className="mt-2 text-sm text-slate-500">Todavía no hay actividades registradas.</p>}
      </div>
    </>}
  </section>;
}

function activityLabel(activity: FollowUpActivity): string {
  try {
    const event = JSON.parse(activity.content);
    if (event.event === "manual_contact") return `${event.channel === "phone" ? "Llamada registrada" : "Correo registrado"} · ${outcomeLabels[event.outcome] ?? "Resultado registrado"}${event.note ? `\n${event.note}` : ""}`;
    if (event.event === "task_scheduled") return `Seguimiento programado: ${event.title}${event.due_at ? ` · ${formatDate(event.due_at)}` : ""}`;
    if (event.event === "task_completed") return `Seguimiento completado: ${event.title}`;
  } catch { /* Existing CRM notes are plain text. */ }
  return activity.content;
}
