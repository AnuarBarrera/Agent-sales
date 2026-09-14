import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { apiFetch } from "../../lib/api";

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
  created_at: string;
}

interface LeadResponse { items: LeadItem[]; next_cursor: string | null }

export function LeadsListPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [query, setQuery] = useState(searchParams.get("q") || "");
  const [data, setData] = useState<LeadResponse>({ items: [], next_cursor: null });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

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
        {loading ? <p className="text-gray-500">Cargando leads…</p> : data.items.length === 0 ? <p className="text-gray-500">No hay leads que coincidan con los filtros.</p> : <table className="w-full min-w-[760px] text-left text-sm"><thead><tr className="border-b text-gray-500"><th className="px-3 py-3">Negocio</th><th className="px-3 py-3">Contacto</th><th className="px-3 py-3">Perfil</th><th className="px-3 py-3">Etapa</th><th className="px-3 py-3">Responsable</th><th /></tr></thead><tbody>{data.items.map((lead) => <tr key={lead.id} className="border-b last:border-0 hover:bg-gray-50"><td className="px-3 py-4"><Link className="font-semibold text-blue-700 hover:underline" to={`/leads/${lead.id}`}>{lead.name}</Link><div className="text-gray-500">{lead.address || "Sin dirección"}</div></td><td className="px-3 py-4">{lead.phone || "Sin teléfono"}</td><td className="px-3 py-4"><span className="badge">{lead.profile}</span><div className="text-xs text-gray-500">{lead.validation_status}</div></td><td className="px-3 py-4">{lead.pipeline_stage_name || "Nuevo"}</td><td className="px-3 py-4">{lead.closer_name || lead.setter_name || "Sin asignar"}</td><td className="px-3 py-4 text-right"><Link to={`/leads/${lead.id}`} className="text-blue-700">Abrir</Link></td></tr>)}</tbody></table>}
      </section>
    </div>
  );
}
