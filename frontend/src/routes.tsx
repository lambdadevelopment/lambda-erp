import { type RouteObject } from "react-router-dom";
import { AppShell } from "@/components/layout/app-shell";
import { ProtectedRoute } from "@/components/auth/protected-route";
import Dashboard from "@/pages/dashboard";
import DocumentList from "@/pages/documents/document-list";
import DocumentForm from "@/pages/documents/document-form";
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

export const routes: RouteObject[] = [
  { path: "/login", element: <Login /> },
  { path: "/demo", element: <Demo /> },
  {
    path: "/",
    element: <ProtectedRoute><AppShell /></ProtectedRoute>,
    children: [
      { index: true, element: <Dashboard /> },
      { path: "setup", element: <Setup /> },
      { path: "setup/opening-balances", element: <OpeningBalances /> },
      { path: "tutorial", element: <Tutorial /> },
      { path: "chat", element: <Chat /> },
      { path: "chat/:sessionId", element: <Chat /> },

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
    ],
  },
];
