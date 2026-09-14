import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { apiFetch } from "../../lib/api";

interface Run {
  id: string;
  giro: string;
  location_label: string;
  status: string;
  found_count: number;
  new_count: number;
  duplicate_count: number;
  error_count: number;
  error_summary: string | null;
}

interface RunEvents {
  status: string;
  found_count: number;
  new_count: number;
  duplicate_count: number;
  error_count: number;
  events: Array<{ id: string; event_type: string; created_at: string }>;
}

const terminalStatuses = new Set(["ready", "partial", "failed"]);

export function ProspectionRunPage() {
  const { runId } = useParams<{ runId: string }>();
  const [run, setRun] = useState<Run | null>(null);
  const [events, setEvents] = useState<RunEvents | null>(null);
  const [error, setError] = useState("");
  const timerRef = useRef<number | null>(null);

  useEffect(() => {
    if (!runId) return;
    let cancelled = false;
    const load = async () => {
      try {
        const [runData, eventData] = await Promise.all([
          apiFetch<Run>(`/prospection-runs/${runId}`),
          apiFetch<RunEvents>(`/prospection-runs/${runId}/events`),
        ]);
        if (cancelled) return;
        setRun(runData);
        setEvents(eventData);
        setError("");
        if (terminalStatuses.has(runData.status) && timerRef.current !== null) {
          window.clearInterval(timerRef.current);
          timerRef.current = null;
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "No se pudo cargar la búsqueda");
      }
    };
    void load();
    timerRef.current = window.setInterval(() => void load(), 2000);
    return () => {
      cancelled = true;
      if (timerRef.current !== null) window.clearInterval(timerRef.current);
      timerRef.current = null;
    };
  }, [runId]);

  const status = run?.status || events?.status || "queued";
  return (
    <div className="max-w-4xl mx-auto space-y-6">
      <div><p className="text-sm text-gray-500">Prospección</p><h2 className="text-2xl font-bold text-gray-900">{run?.giro || "Cargando búsqueda…"}</h2><p className="text-gray-600">{run?.location_label}</p></div>
      {error && <p className="text-red-600" role="alert">{error}</p>}
      <section className="card">
        <div className="flex flex-wrap items-center justify-between gap-3"><span className={`status status-${status}`}>{status}</span>{run && <Link className="btn-secondary" to={`/leads?run_id=${run.id}`}>Ver leads</Link>}</div>
        <div className="mt-6 grid grid-cols-2 gap-4 sm:grid-cols-4"><Metric label="Encontrados" value={run?.found_count ?? events?.found_count ?? 0} /><Metric label="Nuevos" value={run?.new_count ?? events?.new_count ?? 0} /><Metric label="Duplicados" value={run?.duplicate_count ?? events?.duplicate_count ?? 0} /><Metric label="Errores" value={run?.error_count ?? events?.error_count ?? 0} /></div>
        {run?.error_summary && <p className="mt-4 text-sm text-red-600">{run.error_summary}</p>}
      </section>
      <section className="card"><h3 className="font-semibold text-gray-900">Actividad de la búsqueda</h3><ol className="mt-4 space-y-3">{events?.events.map((event) => <li key={event.id} className="border-l-2 border-blue-200 pl-3 text-sm"><span className="font-medium">{event.event_type}</span><time className="ml-2 text-gray-500">{new Date(event.created_at).toLocaleString("es-MX")}</time></li>)}{!events?.events.length && <li className="text-sm text-gray-500">Esperando eventos…</li>}</ol></section>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: number }) {
  return <div><p className="text-sm text-gray-500">{label}</p><p className="text-2xl font-semibold text-gray-900">{value}</p></div>;
}
