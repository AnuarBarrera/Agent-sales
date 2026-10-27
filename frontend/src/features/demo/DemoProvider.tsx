import { createContext, useCallback, useContext, useEffect, useState } from "react";
import type { ReactNode } from "react";
import { apiFetch } from "../../lib/api";
import { useAuth } from "../auth/useAuth";
import type { DemoSession } from "./types";

interface DemoContextValue { session: DemoSession | null; loading: boolean; error: string; refresh: () => Promise<void> }
const DemoContext = createContext<DemoContextValue | null>(null);

export function DemoProvider({ children }: { children: ReactNode }) {
  const { demo } = useAuth();
  const [session, setSession] = useState<DemoSession | null>(null);
  const [loading, setLoading] = useState(!!demo?.enabled);
  const [error, setError] = useState("");
  const refresh = useCallback(async () => {
    if (!demo?.enabled) { setSession(null); setLoading(false); return; }
    setLoading(true);
    try { const next = await apiFetch<DemoSession>("/demo/session"); setSession(next); setError(""); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "No se pudo consultar la sesión demo."); }
    finally { setLoading(false); }
  }, [demo?.enabled]);
  useEffect(() => { void refresh(); }, [refresh]);
  return <DemoContext.Provider value={{ session, loading, error, refresh }}>{children}</DemoContext.Provider>;
}

export function useDemoSession() {
  const context = useContext(DemoContext);
  if (!context) throw new Error("useDemoSession must be used inside DemoProvider");
  return context;
}

export function useOptionalDemoSession() {
  const context = useContext(DemoContext);
  return context ?? { session: null, loading: false, error: "", refresh: async () => undefined };
}
