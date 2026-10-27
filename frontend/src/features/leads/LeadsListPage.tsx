import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { apiFetch } from "../../lib/api";
import { getLatestEvaluation } from "../leadFit/api";
import type { FitEvaluationRead } from "../leadFit/types";
import { useOptionalAuth } from "../auth/useAuth";
import { DemoNextStep } from "../demo/DemoNextStep";

interface LeadItem {
  id: string;
  name: string;
  address: string | null;
  phone: string | null;
  website: string | null;
  profile: string;
  validation_status: string;
  pipeline_stage_name: string | null;
  setter_name: string | null;
  closer_name: string | null;
  priority: number;
  next_follow_up_at: string | null;
  last_activity_at: string | null;
  contacted?: boolean;
  created_at: string;
}

interface LeadResponse { items: LeadItem[]; next_cursor: string | null }
type EvaluationSummary =
  | { status: "loading" | "none" | "pending" | "unavailable" | "error" | "unqueried" }
  | { status: "completed"; classification: string; reasons: string[]; coverage: number | null; nextAction: string };

const classificationLabels: Record<string, string> = {
  qualified: "Cumple condiciones revisadas",
  needs_more_research: "Datos por confirmar",
  not_qualified: "No cumple condiciones revisadas",
};

function summarizeEvaluation(evaluation: FitEvaluationRead | null): EvaluationSummary {
  if (!evaluation) return { status: "none" };
  if (evaluation.status === "pending" || evaluation.status === "running") return { status: "pending" };
  if (evaluation.status !== "completed" || !evaluation.classification) return { status: "unavailable" };
  const reasons = Array.isArray(evaluation.result?.reasons)
    ? evaluation.result.reasons.filter((reason): reason is string => typeof reason === "string" && !!reason.trim())
    : [];
  const nextAction = evaluation.classification === "qualified"
    ? "Revisar la evidencia y el canal de contacto antes de decidir."
    : evaluation.classification === "not_qualified"
      ? "Revisar las condiciones evaluadas antes de decidir."
      : "Revisar la información pendiente antes de decidir.";
  return {
    status: "completed",
    classification: classificationLabels[evaluation.classification] ?? "Evaluación completada",
    reasons,
    coverage: typeof evaluation.coverage === "number" && Number.isFinite(evaluation.coverage) ? evaluation.coverage : null,
    nextAction,
  };
}

function EvaluationBrief({ summary }: { summary: EvaluationSummary }) {
  if (summary.status === "loading") return <p className="mt-2 text-xs text-slate-500">Consultando evaluación…</p>;
  if (summary.status === "none") return <p className="mt-2 text-xs text-slate-500">Sin evaluación registrada.</p>;
  if (summary.status === "pending") return <p className="mt-2 text-xs text-slate-600">Evaluación pendiente o en curso.</p>;
  if (summary.status === "unavailable") return <p className="mt-2 text-xs text-amber-800">Evaluación no disponible.</p>;
  if (summary.status === "error") return <p className="mt-2 text-xs text-amber-800">No se pudo consultar la evaluación.</p>;
  if (summary.status === "unqueried") return <p className="mt-2 text-xs text-slate-500">Abre el prospecto para consultar su evaluación.</p>;
  if (summary.status !== "completed") return null;
  return <div className="mt-2 space-y-1 text-xs text-slate-600">
    <p><strong>Evaluación:</strong> {summary.classification}</p>
    <p><strong>Razón:</strong> {summary.reasons.length ? summary.reasons.join(" ") : "La evaluación no reportó una razón."}</p>
    {summary.coverage !== null && <p>Cobertura de evidencia: {summary.coverage}%</p>}
    <p><strong>Siguiente paso:</strong> {summary.nextAction}</p>
  </div>;
}

export function LeadsListPage() {
  const { demo } = useOptionalAuth();
  const demoEnabled = !!demo?.enabled;
  const [searchParams, setSearchParams] = useSearchParams();
  const [query, setQuery] = useState(searchParams.get("q") || "");
  const [data, setData] = useState<LeadResponse>({ items: [], next_cursor: null });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [evaluations, setEvaluations] = useState<Record<string, EvaluationSummary>>({});

  useEffect(() => {
    const controller = new AbortController();
    const params = new URLSearchParams();
    if (query.trim()) params.set("q", query.trim());
    const runId = searchParams.get("run_id");
    if (runId) params.set("run_id", runId);
    setLoading(true);
    void apiFetch<LeadResponse>(`/leads${params.size ? `?${params}` : ""}`, { signal: controller.signal })
      .then((result) => { setData(result); setError(""); })
      .catch((err: unknown) => { if (!controller.signal.aborted) setError(err instanceof Error ? err.message : "No se pudieron cargar los leads"); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [query, searchParams]);

  useEffect(() => {
    if (!demoEnabled) { setEvaluations({}); return; }
    let active = true;
    const requested = data.items.slice(0, 6);
    setEvaluations({});
    void Promise.all(requested.map(async (lead) => {
      try { return [lead.id, summarizeEvaluation(await getLatestEvaluation(lead.id))] as const; }
      catch { return [lead.id, { status: "error" } as const] as const; }
    })).then((results) => { if (active) setEvaluations(Object.fromEntries(results)); });
    return () => { active = false; };
  }, [data.items, demoEnabled]);

  const submitSearch = (event: React.FormEvent) => {
    event.preventDefault();
    const next = new URLSearchParams(searchParams);
    if (query.trim()) next.set("q", query.trim()); else next.delete("q");
    setSearchParams(next);
  };

  return (
    <div className="max-w-7xl mx-auto space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4"><div><p className="text-sm text-gray-500">CRM</p><h2 className="text-2xl font-bold text-gray-900">Leads</h2><p className="text-gray-600">{data.items.length} resultados visibles para tu usuario</p></div><form onSubmit={submitSearch} className="flex gap-2"><input aria-label="Buscar leads" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Nombre, teléfono o dirección" className="input" /><button className="btn-primary" type="submit">Buscar</button></form></div>
      {error && <p role="alert" className="text-red-600">{error}</p>}
      <section className="card overflow-x-auto">
        {loading ? <p className="text-gray-500">Cargando leads…</p> : data.items.length === 0 ? <p className="text-gray-500">No hay leads que coincidan con los filtros.</p> : <table className={`w-full ${demoEnabled ? "min-w-[520px]" : "min-w-[760px]"} text-left text-sm`}><thead><tr className="border-b text-gray-500"><th className="px-3 py-3">Negocio</th><th className="px-3 py-3">Contacto</th>{!demoEnabled && <><th className="px-3 py-3">Perfil</th><th className="px-3 py-3">Etapa</th><th className="px-3 py-3">Responsable</th></>}<th className="px-3 py-3">{demoEnabled ? "Revisión y siguiente paso" : ""}</th></tr></thead><tbody>{data.items.map((lead, index) => <tr key={lead.id} className="border-b last:border-0 hover:bg-gray-50"><td className="px-3 py-4"><Link className="font-semibold text-blue-700 hover:underline" to={`/leads/${lead.id}`}>{lead.name}</Link><div className="text-gray-500">{lead.address || "Sin dirección"}</div>{demoEnabled && <EvaluationBrief summary={index < 6 ? evaluations[lead.id] ?? { status: "loading" } : { status: "unqueried" }} />}</td><td className="px-3 py-4"><div>{lead.phone || "Sin teléfono"}</div>{demoEnabled && lead.website && <div className="text-gray-600">Sitio observado: {lead.website}</div>}</td>{!demoEnabled && <><td className="px-3 py-4"><span className="badge">{lead.profile}</span><div className="text-xs text-gray-500">{lead.validation_status}</div></td><td className="px-3 py-4">{lead.pipeline_stage_name || "Nuevo"}</td><td className="px-3 py-4">{lead.closer_name || lead.setter_name || "Sin asignar"}</td></>}<td className="px-3 py-4 text-right">{demoEnabled ? <DemoNextStep leadId={lead.id} contacted={lead.contacted} nextFollowUpAt={lead.next_follow_up_at} /> : <Link to={`/leads/${lead.id}`} className="text-blue-700">Abrir</Link>}</td></tr>)}</tbody></table>}
      </section>
    </div>
  );
}
