import { useState } from "react";
import { Link } from "react-router-dom";
import { apiFetch } from "../../lib/api";

interface ImportSummary {
  created: number;
  updated: number;
  skipped: number;
  errors: string[];
}

export function LeadImportPage() {
  const [file, setFile] = useState<File | null>(null);
  const [summary, setSummary] = useState<ImportSummary | null>(null);
  const [error, setError] = useState("");
  const [isLoading, setIsLoading] = useState(false);

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setSummary(null);
    if (!file) {
      setError("Selecciona un archivo CSV para continuar.");
      return;
    }

    const formData = new FormData();
    formData.append("file", file);
    setIsLoading(true);
    try {
      const result = await apiFetch<ImportSummary>("/leads/import", {
        method: "POST",
        body: formData,
      });
      setSummary(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo importar el archivo");
    } finally {
      setIsLoading(false);
    }
  }

  return (
    <div className="mx-auto max-w-4xl space-y-8">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="eyebrow">Carga de datos</p>
          <h2 className="page-title">Importar leads</h2>
          <p className="page-subtitle">
            Trae al CRM tu exportación de Google Sheets/n8n sin perder la validación.
          </p>
        </div>
        <Link className="btn-secondary" to="/leads">Ver leads</Link>
      </div>

      <div className="grid gap-5 lg:grid-cols-[1.25fr_0.75fr]">
        <form onSubmit={handleSubmit} className="card space-y-6">
          <div>
            <h3 className="section-title">Selecciona tu archivo</h3>
            <p className="mt-1 text-sm text-slate-500">
              Aceptamos las cabeceras de las exportaciones de búsqueda y validación.
            </p>
          </div>

          <label
            htmlFor="lead-csv"
            className="flex min-h-44 cursor-pointer flex-col items-center justify-center rounded-2xl border border-dashed border-slate-300 bg-slate-50 px-6 text-center transition hover:border-teal-500 hover:bg-teal-50/40"
          >
            <span className="mb-3 grid h-11 w-11 place-items-center rounded-xl bg-teal-100 text-xl text-teal-700">↑</span>
            <span className="font-semibold text-slate-800">
              {file ? file.name : "Elige un CSV exportado desde Google Sheets"}
            </span>
            <span className="mt-1 text-sm text-slate-500">UTF-8, hasta 10 MB</span>
            <input
              id="lead-csv"
              aria-label="Archivo CSV"
              className="sr-only"
              type="file"
              accept=".csv,text/csv"
              onChange={(event) => setFile(event.target.files?.[0] || null)}
              disabled={isLoading}
            />
          </label>

          {error && <p className="rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700" role="alert">{error}</p>}

          <button className="btn-primary w-full" type="submit" disabled={isLoading}>
            {isLoading ? "Importando leads…" : "Importar leads"}
          </button>
        </form>

        <aside className="card-muted space-y-5">
          <div>
            <p className="eyebrow">Qué se conserva</p>
            <h3 className="section-title mt-1">La información útil entra contigo</h3>
          </div>
          <ul className="space-y-3 text-sm text-slate-600">
            <li className="flex gap-3"><span className="text-teal-600">✓</span><span>Datos del negocio, contacto y ubicación.</span></li>
            <li className="flex gap-3"><span className="text-teal-600">✓</span><span>Web, reseñas, presencia, Meta Ads y perfil.</span></li>
            <li className="flex gap-3"><span className="text-teal-600">✓</span><span>Score, contacto y mensajes fríos.</span></li>
            <li className="flex gap-3"><span className="text-teal-600">✓</span><span>Duplicados detectados por Place ID.</span></li>
          </ul>
        </aside>
      </div>

      {summary && (
        <section className="card border-teal-200 bg-teal-50/60" aria-live="polite">
          <p className="eyebrow text-teal-700">Importación terminada</p>
          <div className="mt-4 grid gap-3 sm:grid-cols-3">
            <Summary label="Nuevos" value={summary.created} />
            <Summary label="Actualizados" value={summary.updated} />
            <Summary label="Omitidos" value={summary.skipped} />
          </div>
          {summary.errors.length > 0 && (
            <ul className="mt-4 space-y-1 text-sm text-red-700">
              {summary.errors.map((item) => <li key={item}>{item}</li>)}
            </ul>
          )}
        </section>
      )}
    </div>
  );
}

function Summary({ label, value }: { label: string; value: number }) {
  return <div className="rounded-xl border border-teal-100 bg-white px-4 py-3"><p className="text-xs font-semibold uppercase tracking-wide text-slate-500">{label}</p><p className="mt-1 text-2xl font-bold text-slate-900">{value}</p></div>;
}
