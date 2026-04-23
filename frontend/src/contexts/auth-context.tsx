import { createContext, useContext, useState, useEffect, useCallback, type ReactNode } from "react";
import { api, ApiError } from "@/api/client";

export interface User {
  name: string;
  email: string;
  full_name: string;
  role: "admin" | "manager" | "viewer" | "public_manager";
}

interface AuthContextValue {
  user: User | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<User>;
  register: (email: string, fullName: string, password: string, inviteToken?: string) => Promise<User>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.authMe()
      .then((u) => setUser(u))
      .catch(() => setUser(null))
      .finally(() => setLoading(false));
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    const u = await api.authLogin(email, password);
    setUser(u);
    return u;
  }, []);

  const register = useCallback(async (email: string, fullName: string, password: string, inviteToken?: string) => {
    const u = await api.authRegister({ email, full_name: fullName, password, invite_token: inviteToken });
    setUser(u);
    return u;
  }, []);

  const logout = useCallback(async () => {
    try { await api.authLogout(); } catch { /* ignore */ }
    setUser(null);
  }, []);

  return (
    <AuthContext.Provider value={{ user, loading, login, register, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
