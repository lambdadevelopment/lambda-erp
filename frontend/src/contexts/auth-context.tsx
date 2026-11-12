import { createContext, useContext, useState, useEffect, useCallback, type ReactNode } from "react";
import { api, ApiError } from "@/api/client";

export interface User {
  name: string;
  email: string;
  full_name: string;
  role: "admin" | "manager" | "viewer" | "public_manager";
  // Whether the account has a real email+password. Social-login-only users have
  // none (they can set a first one from Settings). Undefined on older sessions
  // until the next /me refresh.
  has_password?: boolean;
  // Set for a user with an access profile or a record scope: what to leave
  // out of the menus. The server enforces all of it regardless.
  restrictions?: {
    unreadable_types?: string[];
    denied_reports?: string[];
    record_scope?: Record<string, string[]>;
    // Per document type slug: fields hidden from the user, on the document
    // itself ("") and per child table key.
    hidden_fields?: Record<string, Record<string, string[]>>;
  } | null;
}

interface AuthContextValue {
  user: User | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<User>;
  register: (email: string, fullName: string, password: string, inviteToken?: string) => Promise<User>;
  logout: () => Promise<void>;
  refreshUser: () => Promise<void>;
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

  const refreshUser = useCallback(async () => {
    try {
      const u = await api.authMe();
      setUser(u);
    } catch { /* ignore — session check failures are handled elsewhere */ }
  }, []);

  return (
    <AuthContext.Provider value={{ user, loading, login, register, logout, refreshUser }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
