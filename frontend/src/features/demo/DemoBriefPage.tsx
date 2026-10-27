import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { apiFetch } from "../../lib/api";
import { getCurrentPolicy } from "../leadFit/api";
import { useDemoSession } from "./DemoProvider";
import { quotaLabel } from "./demo";

interface Profile { business_name?: string; offer_summary?: string | null; location_or_market?: string | null }
interface ContextVersion { id: string; status: string; content_contract_version: string }
type PolicyStatus = "loading" | "approved" | "missing" | "error" | "blocked";

export function DemoBriefPage() {
  const { session, loading: sessionLoading, error: sessionError, refresh } = useDemoSession();
  const navigate = useNavigate();
  const [profile, setProfile] = useState<Profile | null>(null);
  const [context, setContext] = useState<ContextVersion | null>(null);
  const [policyStatus, setPolicyStatus] = useState<PolicyStatus>("loading");
  const [policyError, setPolicyError] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    setPolicyError("");
    setPolicyStatus("loading");
    try {
      const [business, versions] = await Promise.all([
        apiFetch<Profile>("/seller-profile"), apiFetch<ContextVersion[]>("/sales-playbooks"),
      ]);
      const approvedContext = versions.find((item) => item.status === "approved" && item.content_contract_version === "4.0") ?? null;
      setProfile(business);
      setContext(approvedContext);
      if (!approvedContext) {
        setPolicyStatus("blocked");
        return;
      }
      try {
        const current = await getCurrentPolicy(approvedContext.id);
        setPolicyStatus(current?.policy_digest ? "approved" : "missing");
      } catch (reason) {
        setPolicyError(reason instanceof Error ? reason.message : "No se pudieron consultar las condiciones de búsqueda.");
        setPolicyStatus("error");
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "No se pudo cargar el resumen del negocio.");
      setPolicyStatus("blocked");
    } finally { setLoading(false); }
  }, []);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => { const onFocus = () => { void load(); void refresh(); }; window.addEventListener("focus", onFocus); return () => window.removeEventListener("focus", onFocus); }, [load, refresh]);

  const businessReady = !!profile?.business_name?.trim();
  const contextApproved = !!context;
  const canSearch = businessReady && contextApproved && policyStatus === "approved";
  const searchEnabled = canSearch && !loading && !sessionLoading && !sessionError && !!session?.searches.remaining && !!session.global_search_available;

  return <main className="demo-workspace">
    <header className="demo-page-head"><div><p className="demo-kicker">Espacio de trabajo · ejemplo</p><h1>Tu negocio</h1><p>Revisa el contexto y las condiciones antes de buscar. El ejemplo empieza como borrador; aprobar requiere tu confirmación en cada sección.</p></div><span className="quota-pill">{session ? quotaLabel(session.searches.remaining) : "Consultando cuota…"}</span></header>
    {error && <p role="alert" className="auth-error">{error}</p>}{loading && <p role="status">Consultando estado de revisión…</p>}
    <section className="brief-business"><div><span className="example-stamp">Negocio de ejemplo</span><h2>{profile?.business_name || "Tu Web MX"}</h2><p>{profile?.offer_summary?.trim() || "Oferta pendiente de completar"} · {profile?.location_or_market?.trim() || "Zona pendiente de revisar"}</p></div><span className="draft-state">{contextApproved ? "Contexto aprobado" : "Contexto pendiente"}</span></section>
    <div className="review-grid">
      <article className="review-step"><span className={`review-indicator ${businessReady ? "complete" : ""}`}>{businessReady ? "✓" : "1"}</span><div><h2>Perfil del negocio</h2><p>{businessReady ? "Perfil completo" : "Completa el nombre del negocio para continuar."} El perfil no requiere una aprobación formal.</p><Link className="btn-secondary" to="/seller-profile">Revisar perfil</Link></div></article>
      <article className="review-step"><span className={`review-indicator ${contextApproved ? "complete" : ""}`}>{contextApproved ? "✓" : "2"}</span><div><h2>Contexto comercial</h2><p>{contextApproved ? "Contexto comercial aprobado." : "Falta revisar y aprobar el contexto comercial."}</p><Link className="btn-secondary" to="/sales-playbook">Revisar contexto</Link></div></article>
      <article className="review-step"><span className={`review-indicator ${policyStatus === "approved" ? "complete" : ""}`}>{policyStatus === "approved" ? "✓" : "3"}</span><div><h2>Condiciones de búsqueda</h2>
        {policyStatus === "approved" && <p>Condiciones de búsqueda aprobadas.</p>}
        {policyStatus === "missing" && <p>Falta revisar y aprobar las condiciones de búsqueda.</p>}
        {policyStatus === "blocked" && <p>Aprueba primero un contexto comercial para revisar las condiciones de búsqueda.</p>}
        {policyStatus === "loading" && <p role="status">Consultando estado de las condiciones…</p>}
        {policyStatus === "error" && <p role="alert" className="auth-error">No se pudieron consultar las condiciones de búsqueda: {policyError}</p>}
        <Link className="btn-secondary" to="/sales-playbook">Revisar y aprobar condiciones</Link>
        {policyStatus === "error" && <button type="button" className="btn-secondary ml-2" disabled={loading} onClick={() => void load()}>Reintentar consulta</button>}
      </div></article>
    </div>
    <section className="search-launch"><div><h2>Buscar negocios en Maps</h2><p>La búsqueda se habilita cuando el perfil está completo, el contexto y las condiciones están aprobados, y hay cuota y capacidad disponibles. La cuota se consulta al servidor y no se reinicia al volver a entrar.</p>{searchEnabled && <p className="review-confirmation">Listo para buscar: perfil completo y aprobaciones vigentes.</p>}{sessionLoading && <p role="status">Actualizando cuota en el servidor…</p>}{sessionError && <p role="alert">No se pudo confirmar la cuota en el servidor.</p>}{session && !session.global_search_available && <p role="status">Las búsquedas están pausadas por capacidad global del servicio.</p>}</div><button type="button" className="btn-primary" disabled={!searchEnabled} onClick={() => navigate("/prospection/new")}>Buscar prospectos</button></section>
  </main>;
}
