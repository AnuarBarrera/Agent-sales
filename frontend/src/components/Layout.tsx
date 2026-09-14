import { Outlet, NavLink } from "react-router-dom";
import { useAuth } from "../features/auth/useAuth";

export function Layout() {
  const { logout, user } = useAuth();

  const navItems = [
    { path: "/leads", label: "Inicio / Leads", end: true },
    { path: "/leads/import", label: "Importar leads" },
    { path: "/prospection/new", label: "Nueva Búsqueda" },
    { path: "/pipeline", label: "Pipeline" },
    { path: "/seller-profile", label: "Mi negocio" },
  ];
  const adminNavItems = user?.role === "admin" ? [
    { path: "/admin", label: "Administración", end: true },
    { path: "/admin/team", label: "Equipo" },
    { path: "/admin/assignments", label: "Asignaciones" },
  ] : [];

  const initials = user?.full_name
    .split(" ")
    .map((name) => name[0])
    .slice(0, 2)
    .join("")
    .toUpperCase() || "TW";

  return (
    <div className="min-h-screen bg-canvas text-ink md:flex">
      <aside className="hidden w-64 shrink-0 flex-col bg-ink px-4 py-5 text-slate-200 md:flex">
        <div className="flex items-center gap-3 px-3">
          <span className="grid h-10 w-10 place-items-center rounded-xl bg-teal-500 font-black text-white">TW</span>
          <div><p className="font-bold text-white">Tu Web MX</p><p className="text-xs text-slate-400">CRM comercial</p></div>
        </div>
        <nav className="mt-10 space-y-2">
          {[...navItems, ...adminNavItems].map((item) => (
            <NavLink
              key={item.path}
              to={item.path}
              end={item.end}
              className={({ isActive }) => `flex items-center rounded-xl px-3 py-3 text-sm font-semibold transition ${isActive ? "bg-white/10 text-white" : "text-slate-400 hover:bg-white/5 hover:text-white"}`}
            >
              <span className={`mr-3 h-2 w-2 rounded-full ${item.path === "/leads/import" ? "bg-amber-400" : "bg-teal-400"}`} />
              {item.label}
            </NavLink>
          ))}
        </nav>
        <div className="mt-auto border-t border-white/10 pt-4">
          <div className="flex items-center gap-3 px-2 py-3">
            <span className="grid h-9 w-9 place-items-center rounded-full bg-teal-100 text-xs font-bold text-teal-700">{initials}</span>
            <div className="min-w-0"><p className="truncate text-sm font-semibold text-white">{user?.full_name || "Usuario"}</p><p className="truncate text-xs text-slate-400">{user?.role || "CRM"}</p></div>
          </div>
          <button onClick={logout} className="w-full rounded-xl px-3 py-2 text-left text-sm text-slate-400 transition hover:bg-white/5 hover:text-white">Cerrar sesión</button>
        </div>
      </aside>

      <div className="flex min-h-screen min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-10 border-b border-slate-200/80 bg-canvas/95 px-4 py-4 backdrop-blur md:hidden">
          <div className="flex items-center justify-between gap-4">
            <div className="flex items-center gap-3"><span className="grid h-9 w-9 place-items-center rounded-xl bg-teal-600 text-xs font-black text-white">TW</span><span className="font-bold text-ink">Tu Web MX</span></div>
            <button onClick={logout} className="text-sm font-semibold text-slate-500">Salir</button>
          </div>
          <nav className="mt-4 flex gap-2 overflow-x-auto pb-1">
            {[...navItems, ...adminNavItems].map((item) => <NavLink key={item.path} to={item.path} end={item.end} className={({ isActive }) => `whitespace-nowrap rounded-lg px-3 py-2 text-xs font-semibold ${isActive ? "bg-teal-100 text-teal-700" : "text-slate-500"}`}>{item.label}</NavLink>)}
          </nav>
        </header>
        <main className="w-full max-w-7xl flex-1 px-4 py-8 sm:px-6 lg:mx-auto lg:px-8">
        <Outlet />
        </main>
      </div>
    </div>
  );
}
