import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { apiFetch } from "../../lib/api";

interface Stage { id: string; key: string; label: string; order: number }
interface Lead { id: string; name: string; pipeline_stage_name: string | null; profile: string; closer_name: string | null; setter_name: string | null }

export function PipelinePage() {
  const [stages, setStages] = useState<Stage[]>([]);
  const [leads, setLeads] = useState<Lead[]>([]);
  const [error, setError] = useState("");
  useEffect(() => {
    void Promise.all([apiFetch<Stage[]>("/pipeline/stages"), apiFetch<{ items: Lead[] }>("/leads")])
      .then(([stageData, leadData]) => { setStages(stageData); setLeads(leadData.items); })
      .catch((err: unknown) => setError(err instanceof Error ? err.message : "No se pudo cargar el pipeline"));
  }, []);
  return <div className="max-w-7xl mx-auto space-y-6"><div><p className="text-sm text-gray-500">CRM</p><h2 className="text-2xl font-bold text-gray-900">Pipeline comercial</h2><p className="text-gray-600">Seguimiento de oportunidades por etapa.</p></div>{error && <p role="alert" className="text-red-600">{error}</p>}<div className="grid gap-4 overflow-x-auto pb-4 md:grid-cols-2 xl:grid-cols-5">{stages.map((stage) => <section className="card min-w-[220px]" key={stage.id}><div className="flex items-center justify-between border-b pb-3"><h3 className="font-semibold text-gray-900">{stage.label}</h3><span className="badge">{leads.filter((lead) => (lead.pipeline_stage_name || "Nuevo") === stage.label).length}</span></div><div className="mt-3 space-y-3">{leads.filter((lead) => (lead.pipeline_stage_name || "Nuevo") === stage.label).map((lead) => <Link className="block rounded border border-gray-200 p-3 hover:border-blue-400" to={`/leads/${lead.id}`} key={lead.id}><p className="font-medium text-gray-900">{lead.name}</p><p className="mt-1 text-xs text-gray-500">{lead.closer_name || lead.setter_name || "Sin asignar"}</p></Link>)}</div></section>)}</div></div>;
}
