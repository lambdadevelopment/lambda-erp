import { cn } from "@/lib/utils";

const variantClasses: Record<string, string> = {
  // Primary: brand-coloured fill + a 1px inset top highlight for depth
  // (button-highlight shadow). On press, the button shifts down 1px —
  // tiny detail, large perceived "this is responsive".
  primary:
    "bg-brand text-brand-fg shadow-button-highlight hover:bg-brand/90 active:translate-y-px focus-visible:ring-brand/40",
  secondary:
    "bg-surface text-fg ring-1 ring-line hover:bg-surface-subtle active:translate-y-px focus-visible:ring-fg/20",
  danger:
    "bg-red-600 text-white shadow-button-highlight hover:bg-red-700 active:translate-y-px focus-visible:ring-red-500/40",
  ghost:
    "bg-transparent text-fg-muted hover:bg-surface-subtle hover:text-fg focus-visible:ring-fg/20",
};

const sizeClasses: Record<string, string> = {
  sm: "h-8 px-3 text-sm",
  md: "h-10 px-4 text-sm",
  lg: "h-11 px-5 text-sm",
};

interface ButtonProps {
  variant?: "primary" | "secondary" | "danger" | "ghost";
  size?: "sm" | "md" | "lg";
  disabled?: boolean;
  onClick?: React.MouseEventHandler<HTMLButtonElement>;
  children: React.ReactNode;
  className?: string;
  type?: "button" | "submit" | "reset";
}

export function Button({
  variant = "primary",
  size = "md",
  disabled,
  onClick,
  children,
  className,
  type = "button",
}: ButtonProps) {
  return (
    <button
      type={type}
      disabled={disabled}
      onClick={onClick}
      className={cn(
        // Layout + base interaction. focus-visible (not :focus) so
        // mouse clicks don't leave a permanent ring; only keyboard
        // navigation gets the visible outline.
        "inline-flex items-center justify-center gap-2 rounded-lg font-medium",
        "transition-all duration-150",
        "focus-visible:outline-none focus-visible:ring-2",
        "disabled:cursor-not-allowed disabled:opacity-50 disabled:active:translate-y-0",
        variantClasses[variant],
        sizeClasses[size],
        className,
      )}
    >
      {children}
    </button>
  );
}
