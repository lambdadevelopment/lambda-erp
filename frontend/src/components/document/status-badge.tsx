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
  const variant = STATUS_VARIANT_MAP[status] ?? "secondary";

  return <Badge variant={variant}>{status}</Badge>;
}
