import { type RouteObject } from "react-router-dom";
import { AppShell } from "@/components/layout/app-shell";
import { ProtectedRoute } from "@/components/auth/protected-route";
import Dashboard from "@/pages/dashboard";
import DocumentList from "@/pages/documents/document-list";
import DocumentForm from "@/pages/documents/document-form";
import ProposalForm from "@/pages/proposals/proposal-form";
import MasterList from "@/pages/masters/master-list";
import MasterForm from "@/pages/masters/master-form";
import TrialBalance from "@/pages/reports/trial-balance";
import GeneralLedger from "@/pages/reports/general-ledger";
import StockBalance from "@/pages/reports/stock-balance";
import ProfitLoss from "@/pages/reports/profit-loss";
import BalanceSheet from "@/pages/reports/balance-sheet";
import ArAging from "@/pages/reports/ar-aging";
import ApAging from "@/pages/reports/ap-aging";
import Analytics from "@/pages/reports/analytics";
import Setup from "@/pages/setup";
import Tutorial from "@/pages/tutorial";
import Chat from "@/pages/chat";
import Login from "@/pages/login";
import Demo from "@/pages/demo";
import OpeningBalances from "@/pages/opening-balances";
import Users from "@/pages/admin/users";
import GeneralSettings from "@/pages/admin/settings";
import { getComponent } from "@/lib/component-registry";

// The dashboard (the index route) is resolved through the component registry
// so a customer deployment can swap it via registerComponent("Dashboard", …).
function DashboardRoute() {
  const C = getComponent("Dashboard", Dashboard);
  return <C />;
}

const baseTopRoutes: RouteObject[] = [
  { path: "/login", element: <Login /> },
  { path: "/demo", element: <Demo /> },
];

const baseChildRoutes: RouteObject[] = [
  { index: true, element: <DashboardRoute /> },
  { path: "setup", element: <Setup /> },
  { path: "setup/opening-balances", element: <OpeningBalances /> },
  { path: "tutorial", element: <Tutorial /> },
  { path: "chat", element: <Chat /> },
  { path: "chat/:sessionId", element: <Chat /> },

  // Proposal (Sammelofferte) — a custom builder; the list reuses the generic
  // DocumentList via the `proposal` doctype config. The static "proposal"
  // segment outranks the ":doctype" wildcard below in react-router's matcher.
  { path: "app/proposal/new", element: <ProposalForm /> },
  { path: "app/proposal/:name", element: <ProposalForm /> },

  // Documents
  { path: "app/:doctype", element: <DocumentList /> },
  { path: "app/:doctype/new", element: <DocumentForm /> },
  { path: "app/:doctype/:name", element: <DocumentForm /> },

  // Masters (reuse same routes - the components detect whether it's a doctype or master)
  { path: "masters/:type", element: <MasterList /> },
  { path: "masters/:type/new", element: <MasterForm /> },
  { path: "masters/:type/:name", element: <MasterForm /> },

  // Reports
  { path: "reports/trial-balance", element: <TrialBalance /> },
  { path: "reports/general-ledger", element: <GeneralLedger /> },
  { path: "reports/stock-balance", element: <StockBalance /> },
  { path: "reports/profit-and-loss", element: <ProfitLoss /> },
  { path: "reports/balance-sheet", element: <BalanceSheet /> },
  { path: "reports/ar-aging", element: <ArAging /> },
  { path: "reports/ap-aging", element: <ApAging /> },
  { path: "reports/analytics", element: <Analytics /> },

  // Admin
  { path: "admin/users", element: <Users /> },
  { path: "admin/settings", element: <GeneralSettings /> },
];

const extraTopRoutes: RouteObject[] = [];
const extraChildRoutes: RouteObject[] = [];

/**
 * Register a route from a customer deployment.
 *  - area "app" (default): added under the protected app shell (sidebar +
 *    header), so it's an authenticated page like the core ones.
 *  - area "top": added at the root (e.g. a public/unauthenticated page).
 *
 * A registered route whose `path` (or index) matches a core route replaces
 * it — that's how you override a core page wholesale.
 */
export function registerRoute(route: RouteObject, opts?: { area?: "app" | "top" }) {
  if (opts?.area === "top") {
    extraTopRoutes.push(route);
  } else {
    extraChildRoutes.push(route);
  }
}

const routeKey = (r: RouteObject) => (r.index ? "__index__" : r.path ?? "");

function mergeRoutes(base: RouteObject[], extra: RouteObject[]): RouteObject[] {
  const out = [...base];
  for (const r of extra) {
    const k = routeKey(r);
    const i = out.findIndex((m) => routeKey(m) === k);
    if (i >= 0) out[i] = r;
    else out.push(r);
  }
  return out;
}

/**
 * Build the full route table, merging any customer-registered routes over the
 * core ones. Call this when constructing the router (the demo does so via
 * bootstrap) — after plugins have registered, so overrides take effect.
 */
export function buildRoutes(): RouteObject[] {
  return [
    ...mergeRoutes(baseTopRoutes, extraTopRoutes),
    {
      path: "/",
      element: <ProtectedRoute><AppShell /></ProtectedRoute>,
      children: mergeRoutes(baseChildRoutes, extraChildRoutes),
    },
  ];
}
