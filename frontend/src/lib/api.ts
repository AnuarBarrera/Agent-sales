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
    try {
      const payload = await response.json();
      message = payload.detail || payload.error?.message || message;
    } catch {
      // Keep the HTTP status when the server did not return JSON.
    }
    throw new Error(message);
  }

  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}
