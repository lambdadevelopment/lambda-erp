import { useEffect, useState } from "react";
import { Outlet, useLocation } from "react-router-dom";
import { ChatProvider, useChat } from "@/components/chat/chat-provider";
import { Sidebar, FLASH_STYLES } from "@/components/layout/sidebar";
import { useAuth } from "@/contexts/auth-context";
import { cn } from "@/lib/utils";

const TITLE_MAP: Record<string, string> = {
  "trial-balance": "Trial Balance",
  "general-ledger": "General Ledger",
  "stock-balance": "Stock Balance",
  "profit-and-loss": "Profit & Loss",
  "balance-sheet": "Balance Sheet",
  "ar-aging": "Accounts Receivable Aging",
  "ap-aging": "Accounts Payable Aging",
  "sales-order": "Sales Order",
  "sales-invoice": "Sales Invoice",
  "purchase-order": "Purchase Order",
  "purchase-invoice": "Purchase Invoice",
  "payment-entry": "Payment Entry",
  "journal-entry": "Journal Entry",
  "stock-entry": "Stock Entry",
  "delivery-note": "Delivery Note",
  "purchase-receipt": "Purchase Receipt",
  "pos-invoice": "POS Invoice",
  "pricing-rule": "Pricing Rule",
  "bank-transaction": "Bank Transaction",
};

function deriveTitle(pathname: string): string {
  const parts = pathname.split("/").filter(Boolean);

  if (parts[0] === "setup") return "Company Setup";
  if (parts[0] === "tutorial") return "Getting Started";
  if (parts[0] === "chat") return "Chat";

  if (parts[0] === "reports") {
    return TITLE_MAP[parts[1]] || parts[1]?.replace(/-/g, " ").replace(/\b\w/g, c => c.toUpperCase()) || "Reports";
  }

  if (parts[0] === "masters") {
    const type = parts[1] || "";
    const label = type.charAt(0).toUpperCase() + type.slice(1);
    if (parts[2] === "new") return `New ${label}`;
    if (parts[2]) return `${label}: ${decodeURIComponent(parts[2])}`;
    return `${label}s`;
  }

  if (parts[0] === "app") {
    const slug = parts[1] || "";
    const label = TITLE_MAP[slug] || slug.replace(/-/g, " ").replace(/\b\w/g, c => c.toUpperCase());
    if (parts[2] === "new") return `New ${label}`;
    if (parts[2]) return `${label}: ${decodeURIComponent(parts[2])}`;
    return `${label}s`;
  }

  return "Dashboard";
}

export function AppShell() {
  return (
    <ChatProvider>
      <AppShellContent />
    </ChatProvider>
  );
}

function AppShellContent() {
  const { pathname } = useLocation();
  const { user, logout } = useAuth();
  const { navigationFlash } = useChat();
  const pageTitle = deriveTitle(pathname);
  const [mobileOpen, setMobileOpen] = useState(false);

  // Auto-close the mobile sidebar on navigation
  useEffect(() => {
    setMobileOpen(false);
  }, [pathname]);

  // When a tool fires a navigation flash and the sidebar is hidden (mobile),
  // pulse the hamburger button in the group's flash color instead.
  const flashStyle = navigationFlash ? FLASH_STYLES[navigationFlash.group] : null;

  return (
    <div className="flex h-dvh bg-gray-50">
      <Sidebar isMobileOpen={mobileOpen} onClose={() => setMobileOpen(false)} />
      {/* Mobile backdrop — only shown when sidebar is open on small screens */}
      {mobileOpen && (
        <div
          onClick={() => setMobileOpen(false)}
          className="fixed inset-0 z-30 bg-black/40 md:hidden"
          aria-hidden="true"
        />
      )}
      <div className="flex flex-1 flex-col overflow-hidden md:ml-64">
        <header className="flex h-10 shrink-0 items-center justify-between border-b border-gray-200 bg-white px-4 md:px-6">
          <div className="flex items-center gap-3">
            <button
              onClick={() => setMobileOpen(true)}
              className={cn(
                "rounded p-1 text-gray-500 transition-all duration-300 hover:bg-gray-100 hover:text-gray-900 md:hidden",
                flashStyle,
              )}
              aria-label="Open menu"
            >
              <svg viewBox="0 0 20 20" fill="currentColor" width="22" height="22">
                <path d="M6.835 4c-.451.004-.82.012-1.137.038-.386.032-.659.085-.876.162l-.2.086c-.44.224-.807.564-1.063.982l-.103.184c-.126.247-.206.562-.248 1.076-.043.523-.043 1.19-.043 2.135v2.664c0 .944 0 1.612.043 2.135.042.515.122.829.248 1.076l.103.184c.256.418.624.758 1.063.982l.2.086c.217.077.49.13.876.162.316.026.685.034 1.136.038zm11.33 7.327c0 .922 0 1.654-.048 2.243-.043.522-.125.977-.305 1.395l-.082.177a4 4 0 0 1-1.473 1.593l-.276.155c-.465.237-.974.338-1.57.387-.59.048-1.322.048-2.244.048H7.833c-.922 0-1.654 0-2.243-.048-.522-.042-.977-.126-1.395-.305l-.176-.082a4 4 0 0 1-1.594-1.473l-.154-.275c-.238-.466-.34-.975-.388-1.572-.048-.589-.048-1.32-.048-2.243V8.663c0-.922 0-1.654.048-2.243.049-.597.15-1.106.388-1.571l.154-.276a4 4 0 0 1 1.594-1.472l.176-.083c.418-.18.873-.263 1.395-.305.589-.048 1.32-.048 2.243-.048h4.334c.922 0 1.654 0 2.243.048.597.049 1.106.15 1.571.388l.276.154a4 4 0 0 1 1.473 1.594l.082.176c.18.418.262.873.305 1.395.048.589.048 1.32.048 2.243zm-10 4.668h4.002c.944 0 1.612 0 2.135-.043.514-.042.829-.122 1.076-.248l.184-.103c.418-.256.758-.624.982-1.063l.086-.2c.077-.217.13-.49.162-.876.043-.523.043-1.19.043-2.135V8.663c0-.944 0-1.612-.043-2.135-.032-.386-.085-.659-.162-.876l-.086-.2a2.67 2.67 0 0 0-.982-1.063l-.184-.103c-.247-.126-.562-.206-1.076-.248-.523-.043-1.19-.043-2.135-.043H8.164L8.165 4z"/>
              </svg>
            </button>
            <h1 className="text-base font-semibold text-gray-500 md:text-lg">{pageTitle}</h1>
          </div>
          <div className="flex items-center gap-3">
            <span className="hidden text-sm text-gray-500 sm:inline">
              {user?.full_name}
              <span className="ml-1.5 rounded bg-gray-100 px-1.5 py-0.5 text-xs font-medium text-gray-600">
                {user?.role}
              </span>
            </span>
            <button
              onClick={logout}
              className="text-sm text-gray-500 hover:text-gray-700"
            >
              Logout
            </button>
          </div>
        </header>
        <main className="flex-1 overflow-auto p-4 md:p-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
