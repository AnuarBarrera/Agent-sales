export class ApiError extends Error {
  readonly status: number;
  readonly code?: string;
  readonly runId?: string;

  constructor(status: number, message: string, code?: string, runId?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.runId = runId;
  }
}

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const method = (init.method || "GET").toUpperCase();
  const csrfToken = window.sessionStorage.getItem("csrf_token");
  const isFormData = typeof FormData !== "undefined" && init.body instanceof FormData;
  const response = await fetch(`/api/v1${path}`, {
    ...init,
    credentials: "include",
    headers: {
      Accept: "application/json",
      ...(init.body && !isFormData ? { "Content-Type": "application/json" } : {}),
      ...(method !== "GET" && csrfToken ? { "X-CSRF-Token": csrfToken } : {}),
      ...init.headers,
    },
  });

  if (!response.ok) {
    let message = `Error ${response.status}`;
    let code: string | undefined;
    let runId: string | undefined;
    try {
      const payload = await response.json();
      message = payload.detail?.message || payload.detail || payload.error?.message || message;
      code = payload.detail?.code;
      runId = payload.run_id ?? payload.detail?.run_id;
    } catch {
      // Keep the HTTP status when the server did not return JSON.
    }
    throw new ApiError(response.status, String(message), code, runId);
  }

  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}
