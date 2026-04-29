import { cn } from "@/lib/utils";

// Tinted-background + ring-on-inset + leading dot pattern. Reads as
// "modern enterprise" (Linear, Stripe) compared to the previous
// solid bg-color-100 / text-color-800 chips.
//
// Each variant has three matched tones: a near-white tint as the
// background, a saturated text colour, and a slightly darker ring.
const variantClasses: Record<string, string> = {
  default:
    "bg-brand/10 text-brand ring-1 ring-inset ring-brand/20",
  success:
    "bg-emerald-50 text-emerald-700 ring-1 ring-inset ring-emerald-200",
  warning:
    "bg-amber-50 text-amber-700 ring-1 ring-inset ring-amber-200",
  danger:
    "bg-rose-50 text-rose-700 ring-1 ring-inset ring-rose-200",
  secondary:
    "bg-surface-subtle text-fg-muted ring-1 ring-inset ring-line",
};

interface BadgeProps {
  variant?: "default" | "success" | "warning" | "danger" | "secondary";
  /** Show a leading status dot. Off by default; turn on for status
   *  pills (Draft / Submitted / Cancelled etc.). */
  dot?: boolean;
  children: React.ReactNode;
  className?: string;
}

export function Badge({ variant = "default", dot, children, className }: BadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium",
        variantClasses[variant],
        className,
      )}
    >
      {dot && <span className="h-1.5 w-1.5 rounded-full bg-current" />}
      {children}
    </span>
  );
}
