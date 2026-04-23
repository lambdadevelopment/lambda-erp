import { cn } from "@/lib/utils";

const variantClasses: Record<string, string> = {
  default: "bg-blue-100 text-blue-800",
  success: "bg-green-100 text-green-800",
  warning: "bg-amber-100 text-amber-800",
  danger: "bg-red-100 text-red-800",
  secondary: "bg-gray-100 text-gray-800",
};

interface BadgeProps {
  variant?: "default" | "success" | "warning" | "danger" | "secondary";
  children: React.ReactNode;
}

export function Badge({ variant = "default", children }: BadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium",
        variantClasses[variant],
      )}
    >
      {children}
    </span>
  );
}
