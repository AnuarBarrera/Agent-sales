export interface DemoRunState { status: string; found_count: number; new_count?: number; duplicate_count?: number }
export interface DemoFitState { pending: number; items?: unknown[]; total?: number; evaluation_unavailable_count?: number }
export interface DemoSearchPayload {
  giro: string; discovery_strategy: "maps"; search_query: string; fit_policy_id: string; candidate_limit: number; enrich_candidates?: boolean;
  location_label: string; center_latitude: number; center_longitude: number; radius_km: number | null; polygon_geometry: unknown | null;
}
export interface DemoSearchIntent { key: string; workspace_id: string; body: DemoSearchPayload }

const demoSearchIntentKey = "tuwebmx:demo:pending-search:v1";

export function readDemoSearchIntent(storage: Pick<Storage, "getItem">, workspaceId: string): DemoSearchIntent | null {
  try {
    const raw = storage.getItem(demoSearchIntentKey);
    if (!raw) return null;
    const value = JSON.parse(raw) as Partial<DemoSearchIntent>;
    const body = value.body;
    if (typeof value.key !== "string" || !/^[0-9a-f-]{36}$/i.test(value.key) || value.workspace_id !== workspaceId || !body || body.discovery_strategy !== "maps"
      || typeof body.giro !== "string" || typeof body.search_query !== "string" || typeof body.fit_policy_id !== "string"
      || !Number.isInteger(body.candidate_limit) || body.candidate_limit < 1 || body.candidate_limit > 3
      || (body.enrich_candidates !== undefined && typeof body.enrich_candidates !== "boolean")
      || typeof body.location_label !== "string" || !Number.isFinite(body.center_latitude) || !Number.isFinite(body.center_longitude)
      || (body.radius_km !== null && !Number.isFinite(body.radius_km))) return null;
    return value as DemoSearchIntent;
  } catch { return null; }
}

export function writeDemoSearchIntent(storage: Pick<Storage, "setItem">, intent: DemoSearchIntent): void {
  try { storage.setItem(demoSearchIntentKey, JSON.stringify(intent)); } catch { /* In-memory key remains stable until this page closes. */ }
}

export function clearDemoSearchIntent(storage: Pick<Storage, "removeItem">): void {
  try { storage.removeItem(demoSearchIntentKey); } catch { /* A known result still clears the in-memory intent. */ }
}

const terminalRuns = new Set(["ready", "partial", "failed"]);

export function isDemoRunSettled(run: DemoRunState, fit: DemoFitState | null): boolean {
  if (!terminalRuns.has(run.status)) return false;
  const expectedEvaluations = typeof run.new_count === "number" && typeof run.duplicate_count === "number"
    ? run.new_count + run.duplicate_count
    : run.found_count;
  if (expectedEvaluations > 0 && !fit) return false;
  const evaluationsObserved = Math.max(fit?.total ?? 0, fit?.items?.length ?? 0) + (fit?.evaluation_unavailable_count ?? 0);
  if (expectedEvaluations > 0 && fit && evaluationsObserved < expectedEvaluations) return false;
  return !fit || fit.pending === 0;
}

export function quotaLabel(remaining: number): string {
  if (remaining <= 0) return "Límite de búsquedas alcanzado";
  return `${remaining} búsqueda${remaining === 1 ? "" : "s"} disponible${remaining === 1 ? "" : "s"}`;
}
