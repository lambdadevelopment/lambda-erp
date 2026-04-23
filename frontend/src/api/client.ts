const BASE = "/api";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

function formatErrorDetail(detail: unknown): string {
  if (!detail) return "Request failed";
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const parts = detail.map((item) => {
      if (!item || typeof item !== "object") return String(item);
      const rec = item as Record<string, unknown>;
      const loc = Array.isArray(rec.loc) ? rec.loc.join(".") : "";
      const msg = typeof rec.msg === "string" ? rec.msg : JSON.stringify(rec);
      return loc ? `${loc}: ${msg}` : msg;
    });
    return parts.join("; ");
  }
  if (typeof detail === "object") {
    const rec = detail as Record<string, unknown>;
    if (typeof rec.msg === "string") return rec.msg;
    return JSON.stringify(detail);
  }
  return String(detail);
}

export async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    ...options,
  });
  if (!res.ok) {
    if (res.status === 401 && !path.startsWith("/auth/")) {
      window.location.href = "/login";
      throw new ApiError(401, "Session expired");
    }
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    throw new ApiError(res.status, formatErrorDetail(body.detail));
  }
  return res.json();
}

function qs(params?: Record<string, string | number | undefined>) {
  if (!params) return "";
  const clean = Object.fromEntries(
    Object.entries(params).filter(([, v]) => v !== undefined && v !== ""),
  );
  const s = new URLSearchParams(clean as Record<string, string>).toString();
  return s ? `?${s}` : "";
}

export const api = {
  // Generic request (used by sidebar for chat sessions)
  request,

  // Chat attachments
  uploadChatAttachment: async (sessionId: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    form.append("session_id", sessionId);
    const res = await fetch(`${BASE}/chat/attachments`, {
      method: "POST",
      credentials: "include",
      body: form,
    });
    if (!res.ok) {
      if (res.status === 401) {
        window.location.href = "/login";
        throw new ApiError(401, "Session expired");
      }
      const body = await res.json().catch(() => ({ detail: res.statusText }));
      throw new ApiError(res.status, body.detail || "Upload failed");
    }
    return res.json() as Promise<{
      id: string;
      filename: string;
      mime_type: string;
      size_bytes: number;
      created_at: string;
    }>;
  },
  getChatAttachmentUrl: (id: string) => `${BASE}/chat/attachments/${encodeURIComponent(id)}`,

  // Chat
  createChatSession: () =>
    request<{ id: string; title: string; created_at: string; updated_at: string; last_message_at?: string | null }>(
      "/chat/sessions",
      { method: "POST" },
    ),

  getChatSession: (id: string) =>
    request<{ id: string; title: string; created_at: string; updated_at: string; last_message_at?: string | null; detail?: string }>(
      `/chat/sessions/${encodeURIComponent(id)}`,
    ),

  // Documents
  listDocuments: (doctype: string, params?: Record<string, string | number | undefined>) =>
    request<{ rows: any[]; total: number; limit: number; offset: number }>(
      `/documents/${doctype}${qs(params)}`,
    ),

  getDocument: (doctype: string, name: string) =>
    request<any>(`/documents/${doctype}/${encodeURIComponent(name)}`),

  createDocument: (doctype: string, data: any) =>
    request<any>(`/documents/${doctype}`, { method: "POST", body: JSON.stringify(data) }),

  updateDocument: (doctype: string, name: string, data: any) =>
    request<any>(`/documents/${doctype}/${encodeURIComponent(name)}`, {
      method: "PUT",
      body: JSON.stringify(data),
    }),

  submitDocument: (doctype: string, name: string) =>
    request<any>(`/documents/${doctype}/${encodeURIComponent(name)}/submit`, { method: "POST" }),

  cancelDocument: (doctype: string, name: string) =>
    request<any>(`/documents/${doctype}/${encodeURIComponent(name)}/cancel`, { method: "POST" }),

  convertDocument: (doctype: string, name: string, targetDoctype: string) =>
    request<any>(`/documents/${doctype}/${encodeURIComponent(name)}/convert`, {
      method: "POST",
      body: JSON.stringify({ target_doctype: targetDoctype }),
    }),

  // Masters
  listMasters: (type: string, params?: Record<string, string | number | undefined>) =>
    request<{ rows: any[]; total: number; limit: number; offset: number }>(
      `/masters/${type}${qs(params)}`,
    ),

  searchMasters: (type: string, q: string) =>
    request<any[]>(`/masters/${type}/search?q=${encodeURIComponent(q)}`),

  searchDocuments: (doctype: string, q: string) =>
    request<any[]>(`/documents/${doctype}/search?q=${encodeURIComponent(q)}`),

  searchLink: async (type: string, q: string): Promise<any[]> => {
    try {
      const results = await request<any[]>(`/masters/${type}/search?q=${encodeURIComponent(q)}`);
      if (results.length > 0) return results;
    } catch {
      // masters endpoint failed, try documents
    }
    return request<any[]>(`/documents/${type}/search?q=${encodeURIComponent(q)}`);
  },

  getMaster: (type: string, name: string) =>
    request<any>(`/masters/${type}/${encodeURIComponent(name)}`),

  createMaster: (type: string, data: any) =>
    request<any>(`/masters/${type}`, { method: "POST", body: JSON.stringify(data) }),

  updateMaster: (type: string, name: string, data: any) =>
    request<any>(`/masters/${type}/${encodeURIComponent(name)}`, {
      method: "PUT",
      body: JSON.stringify(data),
    }),

  deleteMaster: (type: string, name: string) =>
    request<any>(`/masters/${type}/${encodeURIComponent(name)}`, { method: "DELETE" }),

  // Reports
  trialBalance: (params?: Record<string, string>) =>
    request<{ rows: any[]; total_debit: number; total_credit: number; difference: number }>(
      `/reports/trial-balance${qs(params)}`,
    ),

  generalLedger: (params?: Record<string, string>) =>
    request<{
      rows: any[];
      total: number;
      limit: number;
      offset: number;
      opening_balance: number;
    }>(`/reports/general-ledger${qs(params)}`),

  stockBalance: (params?: Record<string, string>) =>
    request<{ rows: any[] }>(`/reports/stock-balance${qs(params)}`),

  dashboardSummary: (company?: string) =>
    request<any>(`/reports/dashboard-summary${qs({ company })}`),

  profitAndLoss: (params?: Record<string, string>) =>
    request<any>(`/reports/profit-and-loss${qs(params)}`),

  balanceSheet: (params?: Record<string, string>) =>
    request<any>(`/reports/balance-sheet${qs(params)}`),

  arAging: (params?: Record<string, string>) =>
    request<any>(`/reports/ar-aging${qs(params)}`),

  apAging: (params?: Record<string, string>) =>
    request<any>(`/reports/ap-aging${qs(params)}`),

  analyticsMetrics: () =>
    request<{
      metrics: Array<{
        metric: string;
        label: string;
        group_by: string[];
        time_based: boolean;
      }>;
    }>("/reports/analytics/metrics"),

  analytics: (params: Record<string, string>) =>
    request<{
      metric: string;
      metric_label: string;
      group_by: string;
      chart_type: "bar" | "line";
      time_based: boolean;
      from_date: string | null;
      to_date: string | null;
      company: string | null;
      rows: Array<{ label: string; value: number }>;
      total: number;
    }>(`/reports/analytics${qs(params)}`),

  runtimeDatasets: () =>
    request<{
      datasets: Array<{
        dataset: string;
        label: string;
        description: string;
        fields: Record<string, string>;
        filter_fields: string[];
        default_limit: number;
        max_limit: number;
      }>;
    }>("/reports/runtime/datasets"),

  runtimeData: (data: {
    requests: Array<{
      name?: string;
      dataset: string;
      fields?: string[];
      filters?: Record<string, unknown>;
      limit?: number;
    }>;
  }) =>
    request<{
      datasets: Array<{
        name: string;
        dataset: string;
        rows: Array<Record<string, unknown>>;
        fields: Record<string, string>;
        row_count: number;
        truncated: boolean;
        limit: number;
      }>;
    }>("/reports/runtime/data", { method: "POST", body: JSON.stringify(data) }),

  createRuntimeDraft: (data: {
    title: string;
    description?: string;
    data_requests: Array<{
      name?: string;
      dataset: string;
      fields?: string[];
      filters?: Record<string, unknown>;
      limit?: number;
    }>;
    transform_js: string;
  }) =>
    request<{
      id: string;
      title: string;
      description?: string;
      definition: Record<string, unknown>;
      url: string;
      created_by?: string;
    }>("/reports/runtime/drafts", { method: "POST", body: JSON.stringify(data) }),

  getRuntimeDraft: (id: string) =>
    request<{
      id: string;
      title: string;
      description?: string;
      definition: Record<string, unknown>;
      url: string;
      created_by?: string;
      source_chat_session_id?: string;
      created_at?: string;
      updated_at?: string;
    }>(`/reports/runtime/drafts/${encodeURIComponent(id)}`),

  listRuntimeDrafts: () =>
    request<{
      drafts: Array<{
        id: string;
        title: string;
        description?: string;
        created_by?: string;
        source_chat_session_id?: string;
        created_at?: string;
        updated_at?: string;
        url: string;
      }>;
    }>("/reports/runtime/drafts"),

  deleteRuntimeDraft: (id: string) =>
    request<{ deleted: string }>(`/reports/runtime/drafts/${encodeURIComponent(id)}`, {
      method: "DELETE",
    }),

  // Setup
  setupStatus: () => request<{ setup_complete: boolean; companies: any[] }>("/setup/status"),

  createCompany: (data: { name: string; currency?: string }) =>
    request<any>("/setup/company", { method: "POST", body: JSON.stringify(data) }),

  seedDemo: () => request<any>("/setup/seed-demo", { method: "POST" }),

  seedHistory: (data?: { start_date?: string; end_date?: string; seed?: number; intensity?: number }) =>
    request<{ ok: boolean; company: string; start_date: string; end_date: string; stats: Record<string, number> }>(
      "/setup/seed-history",
      { method: "POST", body: JSON.stringify(data ?? {}) },
    ),

  importAccountBalances: (data: any) =>
    request<any>("/setup/opening-balances/accounts", { method: "POST", body: JSON.stringify(data) }),
  importStockBalances: (data: any) =>
    request<any>("/setup/opening-balances/stock", { method: "POST", body: JSON.stringify(data) }),
  importOutstandingInvoices: (data: any) =>
    request<any>("/setup/opening-balances/invoices", { method: "POST", body: JSON.stringify(data) }),

  // Auth
  authSetupStatus: () => request<{ has_users: boolean; registration_open: boolean }>("/auth/setup-status"),
  authRegister: (data: { email: string; full_name: string; password: string; invite_token?: string }) =>
    request<any>("/auth/register", { method: "POST", body: JSON.stringify(data) }),
  authLogin: (email: string, password: string) =>
    request<any>("/auth/login", { method: "POST", body: JSON.stringify({ email, password }) }),
  authLogout: () => request<any>("/auth/logout", { method: "POST" }),
  authMe: () => request<any>("/auth/me"),
  authInvite: (email: string, role: string) =>
    request<any>("/auth/invite", { method: "POST", body: JSON.stringify({ email, role }) }),
  authListUsers: () => request<any[]>("/auth/users"),
  authChangeRole: (userName: string, role: string) =>
    request<any>(`/auth/users/${encodeURIComponent(userName)}/role`, { method: "PUT", body: JSON.stringify({ role }) }),
  authDisableUser: (userName: string) =>
    request<any>(`/auth/users/${encodeURIComponent(userName)}`, { method: "DELETE" }),
  authListInvites: () => request<any[]>("/auth/invites"),
  getPublicManagerStatus: () => request<{ active: boolean; user?: any }>("/auth/public-manager"),
  createPublicManager: () => request<any>("/auth/public-manager", { method: "POST" }),
  removePublicManager: () => request<any>("/auth/public-manager", { method: "DELETE" }),
  getSettings: () => request<Record<string, string>>("/auth/settings"),
  updateSettings: (data: Record<string, string>) =>
    request<Record<string, string>>("/auth/settings", { method: "PUT", body: JSON.stringify(data) }),
};
