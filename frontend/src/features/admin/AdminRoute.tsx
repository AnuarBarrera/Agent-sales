import { ReactNode } from "react";
import { useAuth } from "../auth/useAuth";

export function AdminRoute({ children }: { children: ReactNode }) {
  const { user } = useAuth();

  if (user?.role !== "admin") {
    return (
      <div className="card mx-auto max-w-xl text-center">
        <p className="eyebrow">Administración</p>
        <h1 className="page-title">Acceso restringido</h1>
        <p className="page-subtitle mx-auto">Esta sección está disponible únicamente para administradores del workspace.</p>
      </div>
    );
  }

  return <>{children}</>;
}
