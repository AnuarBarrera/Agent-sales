import { createContext, useContext, useState, useEffect, ReactNode } from "react";
import { apiFetch } from "../../lib/api";

interface User {
  id: string;
  email: string;
  full_name: string;
  role: "admin" | "setter" | "closer";
}

type AuthResponse = {
  user: Omit<User, "role">;
  membership: { role: User["role"] };
};

export function normalizeAuthUser(data: AuthResponse): User {
  return { ...data.user, role: data.membership.role };
}

interface AuthContextType {
  user: User | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    checkAuth();
  }, []);

  async function checkAuth() {
    try {
      const data = await apiFetch<AuthResponse>("/auth/me");
      setUser(normalizeAuthUser(data));
      const csrf = await apiFetch<{ csrf_token: string }>("/auth/csrf");
      window.sessionStorage.setItem("csrf_token", csrf.csrf_token);
    } catch {
      // Ignore errors
    } finally {
      setIsLoading(false);
    }
  }

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
      throw new Error(error.detail || "Credenciales invalidas");
    }

    const data = await response.json();
    if (data.csrf_token) window.sessionStorage.setItem("csrf_token", data.csrf_token);
    const currentUser = await apiFetch<AuthResponse>("/auth/me");
    setUser(normalizeAuthUser(currentUser));
  }

  async function logout() {
    await apiFetch("/auth/logout", {
      method: "POST",
    });
    window.sessionStorage.removeItem("csrf_token");
    setUser(null);
  }

  const value: AuthContextType = {
    user,
    isAuthenticated: !!user,
    isLoading,
    login,
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
