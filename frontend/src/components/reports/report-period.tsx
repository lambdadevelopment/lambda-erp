import { useTranslation } from "react-i18next";
import { formatDate } from "@/lib/utils";

interface ReportPeriodProps {
  from?: string;
  to?: string;
  asOf?: string;
}

/** Displays the applied URL-backed period, not unsubmitted filter input. */
export function ReportPeriod({ from, to, asOf }: ReportPeriodProps) {
  const { t } = useTranslation();

  let label = "";
  if (asOf) {
    label = t("reports.asOfDate", { date: formatDate(asOf) });
  } else if (from && to) {
    label = t("reports.periodRange", { from: formatDate(from), to: formatDate(to) });
  } else if (from) {
    label = t("reports.periodFrom", { from: formatDate(from) });
  } else if (to) {
    label = t("reports.periodThrough", { to: formatDate(to) });
  }

  if (!label) return null;

  return (
    <div className="rounded-lg border border-line bg-surface-subtle px-4 py-2 text-sm font-medium text-fg">
      {label}
    </div>
  );
}
