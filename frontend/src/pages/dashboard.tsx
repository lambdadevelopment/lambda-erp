import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import {
  TrendingUp,
  ArrowDownRight,
  ArrowUpRight,
  Package,
  Receipt,
  ShoppingCart,
  Wallet,
  FileText,
  type LucideIcon,
} from "lucide-react";
import { api } from "@/api/client";
import { Card } from "@/components/ui/card";
import { StatusBadge } from "@/components/document/status-badge";
import { formatCurrency, formatDate } from "@/lib/utils";

// ─── Metric cards ──────────────────────────────────────────────────

interface MetricCardProps {
  title: string;
  value: number | undefined;
  icon: LucideIcon;
  /** Tailwind colour class for the icon halo, e.g. "text-emerald-600
   *  bg-emerald-500/10". Defaults to brand. */
  tone?: string;
}

function MetricCard({ title, value, icon: Icon, tone }: MetricCardProps) {
  const toneClass = tone ?? "text-brand bg-brand/10";
  return (
    <Card>
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="text-sm font-medium text-fg-muted">{title}</p>
          <p className="mt-1.5 text-2xl font-semibold tracking-tight tabular-nums text-fg">
            {formatCurrency(value ?? 0)}
          </p>
        </div>
        <div className={`shrink-0 rounded-lg p-2 ${toneClass}`}>
          <Icon className="h-5 w-5" strokeWidth={2} />
        </div>
      </div>
    </Card>
  );
}

function MetricCardSkeleton() {
  return (
    <Card>
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          <div className="h-4 w-24 animate-pulse rounded bg-surface-subtle" />
          <div className="mt-2.5 h-7 w-32 animate-pulse rounded bg-surface-subtle" />
        </div>
        <div className="h-9 w-9 shrink-0 animate-pulse rounded-lg bg-surface-subtle" />
      </div>
    </Card>
  );
}

// ─── Recent documents ──────────────────────────────────────────────

// Each doctype gets a recognisable icon. Falls back to FileText for
// anything we haven't mapped yet.
const DOCTYPE_ICON: Record<string, LucideIcon> = {
  "Sales Invoice": Receipt,
  "Purchase Invoice": Receipt,
  "Payment Entry": Wallet,
  "Sales Order": ShoppingCart,
  "Quotation": FileText,
};

interface RecentDoc {
  doctype?: string;
  type?: string;          // legacy field name — kept for backward compat
  name: string;
  status: string;
  creation?: string;
  date?: string;          // legacy field name — kept for backward compat
}

function RecentDocumentRow({ doc }: { doc: RecentDoc }) {
  const doctype = doc.doctype ?? doc.type ?? "Document";
  const date = doc.creation ?? doc.date;
  const Icon = DOCTYPE_ICON[doctype] ?? FileText;
  // Doctype slug — e.g. "Sales Invoice" → "sales-invoice", matching the
  // routes defined in routes.tsx and used by the sidebar.
  const slug = doctype.toLowerCase().replace(/\s+/g, "-");
  const href = `/app/${slug}/${encodeURIComponent(doc.name)}`;

  return (
    <Link
      to={href}
      className="grid grid-cols-[auto_1fr_auto_auto] items-center gap-3 rounded-lg px-3 py-2.5 transition-colors hover:bg-surface-subtle"
    >
      <div className="rounded-md bg-surface-subtle p-1.5 text-fg-muted">
        <Icon className="h-4 w-4" strokeWidth={2} />
      </div>
      <div className="min-w-0">
        <div className="truncate text-sm font-medium text-fg">{doc.name}</div>
        <div className="truncate text-xs text-fg-muted">{doctype}</div>
      </div>
      <StatusBadge status={doc.status} />
      <div className="hidden text-xs tabular-nums text-fg-muted sm:block">
        {date ? formatDate(date) : "—"}
      </div>
    </Link>
  );
}

function RecentDocumentRowSkeleton() {
  return (
    <div className="grid grid-cols-[auto_1fr_auto_auto] items-center gap-3 px-3 py-2.5">
      <div className="h-7 w-7 animate-pulse rounded-md bg-surface-subtle" />
      <div className="min-w-0 space-y-1.5">
        <div className="h-3.5 w-32 animate-pulse rounded bg-surface-subtle" />
        <div className="h-3 w-20 animate-pulse rounded bg-surface-subtle" />
      </div>
      <div className="h-5 w-16 animate-pulse rounded-full bg-surface-subtle" />
      <div className="hidden h-3 w-16 animate-pulse rounded bg-surface-subtle sm:block" />
    </div>
  );
}

// ─── Page ──────────────────────────────────────────────────────────

export default function DashboardPage() {
  const { data, isLoading } = useQuery({
    queryKey: ["dashboard-summary"],
    queryFn: () => api.dashboardSummary(),
  });

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {isLoading ? (
          <>
            <MetricCardSkeleton />
            <MetricCardSkeleton />
            <MetricCardSkeleton />
            <MetricCardSkeleton />
          </>
        ) : (
          <>
            <MetricCard
              title="Total Revenue"
              value={data?.total_revenue}
              icon={TrendingUp}
              tone="text-emerald-600 bg-emerald-500/10"
            />
            <MetricCard
              title="Outstanding Receivable"
              value={data?.outstanding_receivable}
              icon={ArrowDownRight}
              tone="text-sky-600 bg-sky-500/10"
            />
            <MetricCard
              title="Outstanding Payable"
              value={data?.outstanding_payable}
              icon={ArrowUpRight}
              tone="text-amber-600 bg-amber-500/10"
            />
            <MetricCard
              title="Total Stock Value"
              value={data?.total_stock_value}
              icon={Package}
              tone="text-brand bg-brand/10"
            />
          </>
        )}
      </div>

      <Card title="Recent Documents">
        <div className="-mx-3 space-y-0.5">
          {isLoading ? (
            <>
              <RecentDocumentRowSkeleton />
              <RecentDocumentRowSkeleton />
              <RecentDocumentRowSkeleton />
              <RecentDocumentRowSkeleton />
              <RecentDocumentRowSkeleton />
            </>
          ) : data?.recent_documents && data.recent_documents.length > 0 ? (
            data.recent_documents.map((doc: RecentDoc, idx: number) => (
              <RecentDocumentRow key={`${doc.name}-${idx}`} doc={doc} />
            ))
          ) : (
            <div className="px-3 py-10 text-center text-sm text-fg-muted">
              No recent documents
            </div>
          )}
        </div>
      </Card>
    </div>
  );
}
