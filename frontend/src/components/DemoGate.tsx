import type { ReactNode } from "react";
import { Navigate } from "react-router-dom";
import { useAuth } from "../features/auth/useAuth";
import { useDemoSession } from "../features/demo/DemoProvider";

export function DemoGate({ children, internalOnly = false }: { children: ReactNode; internalOnly?: boolean }) {
  const { demo } = useAuth();
  const { session, loading } = useDemoSession();
  if (!demo?.enabled) return internalOnly ? <>{children}</> : <Navigate to="/leads" replace />;
  if (internalOnly) return <Navigate to="/demo" replace />;
  if (!session?.enabled && loading) return <div className="card" role="status">Cargando espacio demo…</div>;
  if (!session?.enabled) return <div className="card" role="alert">No se pudo confirmar el acceso a la demo. Actualiza la página para volver a intentarlo.</div>;
  return <>{children}</>;
}
