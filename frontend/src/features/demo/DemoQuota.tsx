import { Link } from "react-router-dom";
import { useDemoSession } from "./DemoProvider";
import { quotaLabel } from "./demo";

export function DemoQuota() {
  const { session, loading, error } = useDemoSession();
  if (error) return <span className="demo-quota" role="alert">Cuota no disponible</span>;
  if (loading) return <span className="demo-quota" role="status">Actualizando cuota…</span>;
  if (!session?.enabled) return loading ? <span className="demo-quota" role="status">Cargando cuota…</span> : null;
  return <Link to="/prospection/new" className="demo-quota" aria-label={quotaLabel(session.searches.remaining)}><span aria-hidden="true" className="quota-mark">↗</span>{quotaLabel(session.searches.remaining)}</Link>;
}
