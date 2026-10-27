import { createContext, useContext, useState, useEffect, ReactNode, useRef } from "react";
import { ApiError, apiFetch } from "../../lib/api";

interface User {
  id: string;
  email: string;
  full_name: string;
  role: "admin" | "setter" | "closer";
}

type AuthResponse = {
  user: Omit<User, "role"> & { role?: User["role"] };
  membership?: { role: User["role"] };
  csrf_token?: string;
  demo?: { enabled: boolean; workspace_id: string } | null;
};

export function normalizeAuthUser(data: AuthResponse): User {
  const role = data.membership?.role ?? data.user.role;
  if (role !== "admin" && role !== "setter" && role !== "closer") throw new Error("El servidor no devolvió un rol de membresía válido");
  return { ...data.user, role };
}

let bootstrapFlight: Promise<AuthResponse | null> | null = null;
async function bootstrapAuth(): Promise<AuthResponse | null> {
  if (!bootstrapFlight) {
    bootstrapFlight = (async () => {
      try {
        const data = await apiFetch<AuthResponse>("/auth/me");
        if (data.csrf_token) window.sessionStorage.setItem("csrf_token", data.csrf_token);
        else {
          const csrf = await apiFetch<{ csrf_token: string }>("/auth/csrf");
          window.sessionStorage.setItem("csrf_token", csrf.csrf_token);
        }
        return { ...data, demo: await resolveDemoMarker(data) };
      } catch {
        return null;
      } finally {
        bootstrapFlight = null;
      }
    })();
  }
  return bootstrapFlight;
}

async function resolveDemoMarker(authResponse: AuthResponse): Promise<AuthResponse["demo"]> {
  if (authResponse.demo?.enabled) return authResponse.demo;
  try {
    const session = await apiFetch<{ enabled: boolean; workspace_id: string }>("/demo/session");
    return session.enabled ? { enabled: true, workspace_id: session.workspace_id } : null;
  } catch {
    return null;
  }
}

interface AuthContextType {
  user: User | null;
  demo: AuthResponse["demo"];
  isAuthenticated: boolean;
  isLoading: boolean;
  login: (email: string, password: string) => Promise<void>;
  verifyDemoOtp: (challengeId: string, code: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [demo, setDemo] = useState<AuthResponse["demo"]>(null);
  const [isLoading, setIsLoading] = useState(true);
  const active = useRef(true);

  useEffect(() => {
    active.current = true;
    void bootstrapAuth().then((data) => {
      if (!active.current) return;
      if (data) { setUser(normalizeAuthUser(data)); setDemo(data.demo ?? null); }
      setIsLoading(false);
    });
    return () => { active.current = false; };
  }, []);

  async function login(email: string, password: string) {
    const response = await fetch("/api/v1/auth/login", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      credentials: "include",
      body: JSON.stringify({ email, password }),
    });

    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail?.message || error.detail || "Credenciales invalidas");
    }

    const data = await response.json();
    if (data.csrf_token) window.sessionStorage.setItem("csrf_token", data.csrf_token);
    const currentUser = await apiFetch<AuthResponse>("/auth/me");
    setUser(normalizeAuthUser(currentUser));
    setDemo(await resolveDemoMarker(currentUser));
  }

  async function verifyDemoOtp(challengeId: string, code: string) {
    const response = await fetch("/api/v1/auth/demo/otp/verify", {
      method: "POST", headers: { "Content-Type": "application/json", Accept: "application/json" },
      credentials: "include", body: JSON.stringify({ challenge_id: challengeId, code }),
    });
    const payload = await response.json();
    if (!response.ok) {
      const detail = payload.detail;
      const code = typeof detail?.code === "string" ? detail.code
        : response.status === 403 && detail === "CSRF token required" ? "csrf_token_required" : undefined;
      throw new ApiError(response.status, detail?.message ?? detail ?? "No se pudo verificar el código", code);
    }
    if (!payload.user || !payload.csrf_token || !payload.demo?.enabled) throw new Error("La sesión demo no se confirmó correctamente");
    window.sessionStorage.setItem("csrf_token", payload.csrf_token);
    const currentUser = await apiFetch<AuthResponse>("/auth/me");
    setUser(normalizeAuthUser(currentUser));
    setDemo(payload.demo ?? await resolveDemoMarker(currentUser));
  }

  async function logout() {
    const refreshCsrf = async () => {
      const csrf = await apiFetch<{ csrf_token: string }>("/auth/csrf");
      window.sessionStorage.setItem("csrf_token", csrf.csrf_token);
    };
    await refreshCsrf();
    try {
      await apiFetch("/auth/logout", { method: "POST" });
    } catch (reason) {
      if (!(reason instanceof ApiError) || reason.status !== 403) throw reason;
      await refreshCsrf();
      await apiFetch("/auth/logout", { method: "POST" });
    }
    window.sessionStorage.removeItem("csrf_token");
    setUser(null);
    setDemo(null);
  }

  const value: AuthContextType = {
    user,
    demo,
    isAuthenticated: !!user,
    isLoading,
    login,
    verifyDemoOtp,
    logout,
  };

  return (
    <AuthContext.Provider value={value}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return context;
}

export function useOptionalAuth() {
  const context = useContext(AuthContext);
  return context ?? {
    user: null, demo: null, isAuthenticated: false, isLoading: false,
    login: async () => { throw new Error("No hay una sesión disponible"); },
    verifyDemoOtp: async () => { throw new Error("No hay una sesión disponible"); },
    logout: async () => undefined,
  };
}
