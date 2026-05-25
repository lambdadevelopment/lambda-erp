import { useTranslation } from "react-i18next";
import { Badge } from "@/components/ui/badge";

type BadgeVariant = "default" | "success" | "warning" | "danger" | "secondary";

const STATUS_VARIANT_MAP: Record<string, BadgeVariant> = {
  Draft: "secondary",
  Open: "default",
  Submitted: "default",
  "To Deliver and Bill": "warning",
  "To Deliver": "warning",
  "To Bill": "warning",
  Completed: "success",
  Ordered: "success",
  Paid: "success",
  Cancelled: "danger",
  Overdue: "danger",
};

interface StatusBadgeProps {
  status: string;
}

export function StatusBadge({ status }: StatusBadgeProps) {
  const { t } = useTranslation();
  // Variant keys off the backend's English status; only the label is localized.
  const variant = STATUS_VARIANT_MAP[status] ?? "secondary";

  return <Badge variant={variant}>{t(`status.${status}`, { defaultValue: status })}</Badge>;
}
