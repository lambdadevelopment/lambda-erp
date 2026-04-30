import { useState, useCallback, useEffect, useMemo } from "react";
import { NavLink, useLocation, useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";

// --- Persist collapsed sidebar groups in localStorage ---
const STORAGE_KEY = "sidebar-collapsed";

function getCollapsed(): Set<string> {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? new Set(JSON.parse(raw)) : new Set();
  } catch {
    return new Set();
  }
}

function setCollapsed(collapsed: Set<string>) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify([...collapsed]));
}

function useSidebarToggle(key: string) {
  const [open, setOpen] = useState(() => !getCollapsed().has(key));
  const toggle = useCallback(() => {
    setOpen((prev) => {
      const collapsed = getCollapsed();
      if (prev) {
        collapsed.add(key);
      } else {
        collapsed.delete(key);
      }
      setCollapsed(collapsed);
      return !prev;
    });
  }, [key]);
  const expand = useCallback(() => {
    setOpen((prev) => {
      if (prev) return prev;
      const collapsed = getCollapsed();
      collapsed.delete(key);
      setCollapsed(collapsed);
      return true;
    });
  }, [key]);
  return [open, toggle, expand] as const;
}
import {
  FileText,
  BarChart3,
  Database,
  Package,
  ShoppingCart,
  CreditCard,
  BookOpen,
  MessageCircle,
  Settings,
  ChevronDown,
  ChevronRight,
  Plus,
  Trash2,
  LineChart,
} from "lucide-react";
import { useMutation } from "@tanstack/react-query";
import { useChat } from "@/components/chat/chat-provider";
import { cn } from "@/lib/utils";

interface NavItem {
  label: string;
  path: string;
}

interface NavGroup {
  label: string;
  icon: React.ReactNode;
  items: NavItem[];
}

export const FLASH_STYLES: Record<string, string> = {
  "Introduction": "bg-fuchsia-300 text-fuchsia-950 ring-2 ring-inset ring-fuchsia-100",
  "Selling": "bg-emerald-300 text-emerald-950 ring-2 ring-inset ring-emerald-100",
  "Buying": "bg-rose-300 text-rose-950 ring-2 ring-inset ring-rose-100",
  "Accounting": "bg-amber-300 text-amber-950 ring-2 ring-inset ring-amber-100",
  "Stock": "bg-blue-300 text-blue-950 ring-2 ring-inset ring-blue-100",
  "Reports": "bg-indigo-300 text-indigo-950 ring-2 ring-inset ring-indigo-100",
  "Masters": "bg-lime-300 text-lime-950 ring-2 ring-inset ring-lime-100",
  "Settings": "bg-purple-300 text-purple-950 ring-2 ring-inset ring-purple-100",
  "Custom Analytics": "bg-indigo-300 text-indigo-950 ring-2 ring-inset ring-indigo-100",
};

const NAV_GROUPS: NavGroup[] = [
  {
    label: "Introduction",
    icon: <BookOpen className="h-4 w-4" />,
    items: [
      { label: "Getting Started", path: "/tutorial" },
      { label: "Company Setup", path: "/setup" },
      { label: "Opening Balances", path: "/setup/opening-balances" },
    ],
  },
  {
    label: "Selling",
    icon: <ShoppingCart className="h-4 w-4" />,
    items: [
      { label: "Quotation", path: "/app/quotation" },
      { label: "Sales Order", path: "/app/sales-order" },
      { label: "Sales Invoice", path: "/app/sales-invoice" },
      { label: "POS Invoice", path: "/app/pos-invoice" },
    ],
  },
  {
    label: "Buying",
    icon: <CreditCard className="h-4 w-4" />,
    items: [
      { label: "Purchase Order", path: "/app/purchase-order" },
      { label: "Purchase Invoice", path: "/app/purchase-invoice" },
    ],
  },
  {
    label: "Accounting",
    icon: <FileText className="h-4 w-4" />,
    items: [
      { label: "Payment Entry", path: "/app/payment-entry" },
      { label: "Journal Entry", path: "/app/journal-entry" },
      { label: "Bank Transaction", path: "/app/bank-transaction" },
      { label: "Budget", path: "/app/budget" },
      { label: "Subscription", path: "/app/subscription" },
    ],
  },
  {
    label: "Stock",
    icon: <Package className="h-4 w-4" />,
    items: [
      { label: "Stock Entry", path: "/app/stock-entry" },
      { label: "Delivery Note", path: "/app/delivery-note" },
      { label: "Purchase Receipt", path: "/app/purchase-receipt" },
    ],
  },
  {
    label: "Reports",
    icon: <BarChart3 className="h-4 w-4" />,
    items: [
      { label: "Trial Balance", path: "/reports/trial-balance" },
      { label: "Profit & Loss", path: "/reports/profit-and-loss" },
      { label: "Balance Sheet", path: "/reports/balance-sheet" },
      { label: "General Ledger", path: "/reports/general-ledger" },
      { label: "AR Aging", path: "/reports/ar-aging" },
      { label: "AP Aging", path: "/reports/ap-aging" },
      { label: "Analytics", path: "/reports/analytics" },
      { label: "Stock Balance", path: "/reports/stock-balance" },
    ],
  },
  {
    label: "Masters",
    icon: <Database className="h-4 w-4" />,
    items: [
      { label: "Company", path: "/masters/company" },
      { label: "Customer", path: "/masters/customer" },
      { label: "Supplier", path: "/masters/supplier" },
      { label: "Item", path: "/masters/item" },
      { label: "Warehouse", path: "/masters/warehouse" },
    ],
  },
  {
    label: "Settings",
    icon: <Settings className="h-4 w-4" />,
    items: [
      { label: "General", path: "/admin/settings" },
      { label: "Pricing Rule", path: "/app/pricing-rule" },
      { label: "Users & Team", path: "/admin/users" },
    ],
  },
];

function ChatGroup() {
  const [open, toggle] = useSidebarToggle("chats");
  const location = useLocation();
  const navigate = useNavigate();
  const { sessions, createSession, deleteSession } = useChat();

  const handleNewChat = async () => {
    try {
      const session = await createSession();
      navigate(`/chat/${session.id}`);
    } catch {
      // ignore
    }
  };

  const handleDelete = async (e: React.MouseEvent, sessionId: string) => {
    e.preventDefault();
    e.stopPropagation();
    try {
      await deleteSession(sessionId);
      // If we deleted the active chat, navigate away
      if (window.location.pathname === `/chat/${sessionId}`) {
        navigate("/");
      }
    } catch {
      // ignore
    }
  };

  return (
    <div>
      <button
        type="button"
        onClick={toggle}
        className="flex w-full items-center gap-2 px-4 py-2 text-xs font-semibold uppercase tracking-wider text-fg-muted transition-colors hover:text-fg"
      >
        <MessageCircle className="h-4 w-4" />
        <span className="flex-1 text-left">Chats</span>
        {open ? (
          <ChevronDown className="h-3.5 w-3.5" />
        ) : (
          <ChevronRight className="h-3.5 w-3.5" />
        )}
      </button>
      {open && (
        <ul className="mb-1">
          {/* New Chat button */}
          <li>
            <button
              onClick={handleNewChat}
              className="flex w-full items-center gap-2 px-4 py-1.5 pl-10 text-sm text-brand transition-colors hover:bg-brand/5"
            >
              <Plus className="h-3.5 w-3.5" />
              New Chat
            </button>
          </li>
          {/* Existing sessions */}
          {sessions.map((session) => {
            const active = location.pathname === `/chat/${session.id}`;
            return (
              <li key={session.id} className="group relative">
                <NavLink
                  to={`/chat/${session.id}`}
                  className={cn(
                    "relative block truncate px-4 py-1.5 pl-10 pr-8 text-sm transition-colors",
                    active
                      ? "bg-surface-subtle font-medium text-fg"
                      : "text-fg-muted hover:bg-surface-subtle hover:text-fg",
                  )}
                >
                  {session.title}
                </NavLink>
                {active && <ActiveAccent />}
                <button
                  onClick={(e) => handleDelete(e, session.id)}
                  className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-0.5 text-fg-muted opacity-0 transition-opacity hover:text-red-500 group-hover:opacity-100"
                  title="Delete chat"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

/** 2px brand-colored accent strip on the left edge — Linear-style
 *  active indicator. Pulled out so all three nav patterns (chats,
 *  custom analytics, generic groups) reuse one definition. */
function ActiveAccent() {
  return (
    <span
      aria-hidden="true"
      className="pointer-events-none absolute left-0 top-1/2 h-5 w-[2px] -translate-y-1/2 rounded-r bg-brand"
    />
  );
}

function CustomAnalyticsGroup() {
  const [open, toggle, expand] = useSidebarToggle("custom-analytics");
  const location = useLocation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { navigationFlash, sessions, createSession } = useChat();
  const isGroupFlashing = navigationFlash?.group === "Custom Analytics";
  const flashStyle = FLASH_STYLES["Custom Analytics"];

  const { data } = useQuery({
    queryKey: ["runtime-drafts"],
    queryFn: () => api.listRuntimeDrafts(),
    staleTime: 30_000,
  });
  const drafts = data?.drafts ?? [];

  // Any flash targeting this group should expand the group, refetch the
  // draft list, and let the row-level flash animation draw attention to
  // the newly-created / touched entry.
  useEffect(() => {
    if (navigationFlash?.group !== "Custom Analytics") return;
    expand();
    queryClient.invalidateQueries({ queryKey: ["runtime-drafts"] });
  }, [navigationFlash?.key, queryClient, expand]);

  const deleteMutation = useMutation({
    mutationFn: (id: string) => api.deleteRuntimeDraft(id),
    onSuccess: (_result, id) => {
      queryClient.invalidateQueries({ queryKey: ["runtime-drafts"] });
      const match = location.search.includes(`report_id=${id}`);
      if (match) navigate("/reports/analytics");
    },
  });

  const handleDelete = async (e: React.MouseEvent, id: string) => {
    e.preventDefault();
    e.stopPropagation();
    deleteMutation.mutate(id);
  };

  const handleNew = async () => {
    const prefill =
      "Build me a custom analytics report: <describe what you want — e.g. top 10 customers by revenue for the last quarter, or monthly purchases trend by supplier>";
    let targetSessionId = sessions[0]?.id || "";
    if (!targetSessionId) {
      try {
        const created = await createSession();
        targetSessionId = created.id;
      } catch {
        navigate("/reports/analytics");
        return;
      }
    }
    navigate(`/chat/${targetSessionId}`, { state: { prefillMessage: prefill } });
  };

  return (
    <div>
      <button
        type="button"
        onClick={toggle}
        className={cn(
          "flex w-full items-center gap-2 px-4 py-2 text-xs font-semibold uppercase tracking-wider text-fg-muted transition-all duration-300 hover:text-fg",
          isGroupFlashing && flashStyle,
        )}
      >
        <LineChart className="h-4 w-4" />
        <span className="flex-1 text-left">Custom Analytics</span>
        {open ? (
          <ChevronDown className="h-3.5 w-3.5" />
        ) : (
          <ChevronRight className="h-3.5 w-3.5" />
        )}
      </button>
      {open && (
        <ul className="mb-1">
          <li>
            <button
              onClick={handleNew}
              className="flex w-full items-center gap-2 px-4 py-1.5 pl-10 text-sm text-brand transition-colors hover:bg-brand/5"
            >
              <Plus className="h-3.5 w-3.5" />
              New Analytics
            </button>
          </li>
          {drafts.map((draft) => {
            const path = `/reports/analytics?report_id=${draft.id}`;
            const active = location.pathname === "/reports/analytics" && location.search.includes(`report_id=${draft.id}`);
            const isItemFlashing =
              navigationFlash?.group === "Custom Analytics" &&
              navigationFlash?.item === draft.id;
            return (
              <li key={draft.id} className="group relative">
                <NavLink
                  to={path}
                  className={cn(
                    "relative block truncate px-4 py-1.5 pl-10 pr-8 text-sm transition-all duration-300",
                    !active && "text-fg-muted hover:bg-surface-subtle hover:text-fg",
                    active && "bg-surface-subtle font-medium text-fg",
                    isItemFlashing && flashStyle,
                  )}
                  title={draft.title}
                >
                  {draft.title}
                </NavLink>
                {active && <ActiveAccent />}
                <button
                  onClick={(e) => handleDelete(e, draft.id)}
                  className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-0.5 text-fg-muted opacity-0 transition-opacity hover:text-red-500 group-hover:opacity-100"
                  title="Delete report"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

function SidebarGroup({ group }: { group: NavGroup }) {
  const [open, toggle] = useSidebarToggle(group.label);
  const { navigationFlash } = useChat();
  const isGroupFlashing = navigationFlash?.group === group.label;
  const flashStyle = FLASH_STYLES[group.label] ?? "bg-blue-300 text-blue-950 ring-2 ring-inset ring-blue-100";

  return (
    <div>
      <button
        type="button"
        onClick={toggle}
        className={cn(
          "flex w-full items-center gap-2 px-4 py-2 text-xs font-semibold uppercase tracking-wider text-fg-muted transition-all duration-300 hover:text-fg",
          isGroupFlashing && flashStyle,
        )}
      >
        {group.icon}
        <span className="flex-1 text-left">{group.label}</span>
        {open ? (
          <ChevronDown className="h-3.5 w-3.5" />
        ) : (
          <ChevronRight className="h-3.5 w-3.5" />
        )}
      </button>
      {open && (
        <ul className="mb-1">
          {group.items.map((item) => (
            <li key={item.path} className="relative">
              <NavLink
                to={item.path}
                className={({ isActive }) =>
                  cn(
                    "relative block px-4 py-1.5 pl-10 text-sm transition-all duration-300",
                    !isActive && "text-fg-muted hover:bg-surface-subtle hover:text-fg",
                    isActive && "bg-surface-subtle font-medium text-fg",
                    navigationFlash?.group === group.label && navigationFlash?.item === item.label &&
                      flashStyle,
                  )
                }
              >
                {({ isActive }) => (
                  <>
                    {isActive && <ActiveAccent />}
                    {item.label}
                  </>
                )}
              </NavLink>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

const HIDDEN_PATHS: Record<string, string> = {
  "/setup/opening-balances": "opening_balances_enabled",
};

interface SidebarProps {
  isMobileOpen?: boolean;
  onClose?: () => void;
}

export function Sidebar({ isMobileOpen = false, onClose }: SidebarProps) {
  const { data: settings } = useQuery({
    queryKey: ["settings"],
    queryFn: () => api.getSettings(),
    staleTime: 60_000,
  });

  const { data: setupStatus } = useQuery({
    queryKey: ["setup-status"],
    queryFn: () => api.setupStatus(),
    staleTime: 60_000,
  });

  const firstCompany = setupStatus?.companies?.[0];
  const companyName: string =
    firstCompany?.company_name || firstCompany?.name || "Lambda ERP";

  const groups = useMemo(() => {
    if (!settings) return NAV_GROUPS;
    return NAV_GROUPS.map((group) => ({
      ...group,
      items: group.items.filter((item) => {
        const settingKey = HIDDEN_PATHS[item.path];
        if (!settingKey) return true;
        return settings[settingKey] !== "0";
      }),
    }));
  }, [settings]);

  return (
    <aside
      className={cn(
        "fixed left-0 top-0 z-40 flex h-dvh w-64 flex-col border-r border-line bg-surface transition-transform duration-300 ease-out",
        "md:translate-x-0",
        isMobileOpen ? "translate-x-0 shadow-2xl" : "-translate-x-full",
      )}
    >
      <div className="relative flex h-10 items-center justify-center gap-3 border-b border-line px-4">
        <NavLink
          to="/"
          className="min-w-0 truncate text-base font-semibold text-fg-muted transition-colors hover:text-brand"
          title={`${companyName} — Dashboard`}
        >
          {companyName}
        </NavLink>
        <div className="hidden h-4 w-px shrink-0 bg-line md:block" aria-hidden="true" />
        <a
          href="https://lambda.dev/erp"
          target="_blank"
          rel="noopener noreferrer"
          aria-label="Lambda ERP"
          title="Lambda ERP"
          className="hidden shrink-0 md:block"
        >
          <span className="lambda-logo-icon block" role="img" aria-label="Lambda ERP" />
        </a>
        {/* Close button — mobile only. Absolute so it doesn't offset centering. */}
        <button
          onClick={onClose}
          className="absolute right-3 top-1/2 -translate-y-1/2 rounded p-1 text-fg-muted transition-colors hover:bg-surface-subtle hover:text-fg md:hidden"
          aria-label="Close menu"
        >
          <svg viewBox="0 0 20 20" width="18" height="18" fill="currentColor">
            <path d="M10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 011.414-1.414L10 8.586z" />
          </svg>
        </button>
      </div>
      <nav className="flex-1 overflow-y-auto py-3">
        <ChatGroup />
        <CustomAnalyticsGroup />
        {groups.map((group) => (
          <SidebarGroup key={group.label} group={group} />
        ))}
      </nav>
    </aside>
  );
}
