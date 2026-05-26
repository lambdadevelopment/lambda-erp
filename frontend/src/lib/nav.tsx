/**
 * Sidebar navigation registry.
 *
 * The default groups below drive the demo app's sidebar. A customer
 * deployment built on the published library can add a group, append items to
 * an existing group, or replace a group wholesale via the registration
 * helpers — without editing the sidebar component. The sidebar reads the
 * registry live (getNavGroups), so registrations made at startup show up.
 */
import type { ReactNode } from "react";
import {
  FileText,
  BarChart3,
  Database,
  Package,
  ShoppingCart,
  CreditCard,
  BookOpen,
  Settings,
} from "lucide-react";

export interface NavItem {
  label: string;
  path: string;
}

export interface NavGroup {
  label: string;
  icon: ReactNode;
  items: NavItem[];
}

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

/** Live list the sidebar renders from. */
export function getNavGroups(): NavGroup[] {
  return NAV_GROUPS;
}

/**
 * Add a nav group, or replace an existing one with the same label. Pass
 * `index` to control placement (default: appended at the end).
 */
export function registerNavGroup(group: NavGroup, opts?: { index?: number }) {
  const existing = NAV_GROUPS.findIndex((g) => g.label === group.label);
  if (existing >= 0) {
    NAV_GROUPS[existing] = group;
    return;
  }
  if (opts?.index != null) {
    NAV_GROUPS.splice(opts.index, 0, group);
  } else {
    NAV_GROUPS.push(group);
  }
}

/** Append an item to an existing group (creating the group if absent). */
export function registerNavItem(groupLabel: string, item: NavItem) {
  const group = NAV_GROUPS.find((g) => g.label === groupLabel);
  if (group) {
    group.items.push(item);
  } else {
    NAV_GROUPS.push({ label: groupLabel, icon: null, items: [item] });
  }
}
