import { useEffect, useRef, useState } from "react";
import { apiFetch } from "../../lib/api";

interface Suggestion {
  status: "available" | "unavailable" | "reserved" | "completed" | "failed";
  remaining: number;
  reason: string | null;
  content: { summary: string; questions: string[]; next_step: string; evidence_ids: string[] } | null;
  sources: { evidence_id: string; url: string; title: string | null }[];
  audit: { provider: string; model: string; tokens: number } | null;
  generated_at: string | null;
}

const changed = "demo-ai-suggestion-changed";

export function DemoAiSuggestionPanel({ leadId }: { leadId: string }) {
  const [data, setData] = useState<Suggestion | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const inFlight = useRef(false);
  const endpoint = `/demo/leads/${encodeURIComponent(leadId)}/ai-suggestion`;

  useEffect(() => {
    let active = true;
    setData(null); setError("");
    const load = () => {
      void apiFetch<Suggestion>(endpoint).then(next => { if (active) { setData(next); setError(""); } })
        .catch((reason: unknown) => { if (active) setError(reason instanceof Error ? reason.message : "No se pudo consultar la sugerencia."); });
    };
    load();
    window.addEventListener(changed, load);
    return () => { active = false; window.removeEventListener(changed, load); };
  }, [endpoint]);

  const suggest = async () => {
    if (inFlight.current || data?.status !== "available") return;
    inFlight.current = true; setBusy(true); setError("");
    try {
      setData(await apiFetch<Suggestion>(endpoint, { method: "POST" }));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "No se pudo completar la sugerencia.");
    } finally {
      inFlight.current = false; setBusy(false);
      // Read-only refresh across cards; it never triggers inference.
      window.dispatchEvent(new Event(changed));
    }
  };

  return <section className="min-w-0 space-y-2 rounded-xl border border-teal-200 bg-teal-50/50 p-3 text-left" aria-label="Sugerencia con IA">
    <h3 className="text-sm font-semibold text-teal-900">Revisar con Nemotron</h3>
    <p className="text-xs text-slate-600">Hasta dos leads por participante. La sugerencia no cambia la evaluación ni autoriza contacto.</p>
    {error && <p role="alert" className="text-xs text-red-700">{error}</p>}
    {!data && !error && <p className="text-xs text-slate-500">Consultando disponibilidad…</p>}
    {data && <>
      <p className="text-xs text-slate-600">{data.remaining} de 2 sugerencias disponibles</p>
      {data.status === "available" && <>
        <button className="btn-secondary" type="button" disabled={busy} onClick={() => void suggest()}>{busy ? "Consultando Nemotron…" : "Sugerir con IA"}</button>
        <p className="text-xs text-slate-500">Un intento por lead, sin reintentos automáticos. Los intentos fallidos también cuentan.</p>
      </>}
      {data.reason && <p role="status" className="text-xs text-amber-800">{data.reason}</p>}
      {data.content && <div className="min-w-0 space-y-2 break-words text-sm text-slate-700">
        <p className="font-semibold">Sugerencia para revisión humana</p>
        <p>{data.content.summary}</p>
        <ul className="list-disc space-y-1 pl-4">{data.content.questions.map((q, i) => <li key={i}>{q}</li>)}</ul>
        <p><strong>Próximo paso sugerido:</strong> {data.content.next_step}</p>
        <details><summary className="cursor-pointer text-xs text-teal-800">Fuentes consultadas y modelo</summary>
          <ul className="mt-2 space-y-1">{data.sources.filter(s => data.content?.evidence_ids.includes(s.evidence_id)).map(s => <li key={s.evidence_id}>
            {/^https?:\/\//i.test(s.url) ? <a className="text-xs text-blue-700 underline" href={s.url} target="_blank" rel="noreferrer">{s.title || "Fuente del prospecto"}</a> : <span className="text-xs">Fuente del prospecto</span>}
          </li>)}</ul>
          {data.audit && <p className="mt-2 break-all text-xs text-slate-500">{data.audit.model} · Nebius Token Factory · {data.audit.tokens} tokens</p>}
          {data.generated_at && <p className="text-xs text-slate-500">Generada el {new Date(data.generated_at).toLocaleString("es-MX")}. Basada en las fuentes disponibles en ese momento.</p>}
        </details>
      </div>}
    </>}
  </section>;
}
