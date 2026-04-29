import { cn } from "@/lib/utils";

interface InputProps extends React.InputHTMLAttributes<HTMLInputElement> {
  label?: string;
  /** Optional helper text below the input. */
  hint?: string;
  /** Error text — when set, overrides hint and shows a red border. */
  error?: string;
}

export function Input({ label, hint, error, className, id, ...props }: InputProps) {
  const inputId = id || (label ? label.toLowerCase().replace(/\s+/g, "-") : undefined);

  return (
    <div>
      {label && (
        <label
          htmlFor={inputId}
          className="mb-1.5 block text-sm font-medium text-fg"
        >
          {label}
        </label>
      )}
      <input
        id={inputId}
        aria-invalid={!!error || undefined}
        aria-describedby={error || hint ? `${inputId}-help` : undefined}
        className={cn(
          // Bigger touch target (h-10) than the previous py-2 default;
          // brand-coloured halo on focus instead of a single hard ring.
          // text-sm is fine on desktop; the global media-query in
          // index.css forces 16px on mobile to suppress iOS auto-zoom.
          "block h-10 w-full rounded-lg bg-surface px-3 text-sm text-fg",
          "ring-1 ring-line transition-all",
          "placeholder:text-fg-muted/70",
          "focus:outline-none focus:ring-2 focus:ring-brand/30 focus:border-transparent",
          "disabled:cursor-not-allowed disabled:bg-surface-subtle disabled:text-fg-muted",
          error && "ring-red-400 focus:ring-red-400/30",
          className,
        )}
        {...props}
      />
      {(error || hint) && (
        <p
          id={`${inputId}-help`}
          className={cn(
            "mt-1.5 text-xs",
            error ? "text-red-600" : "text-fg-muted",
          )}
        >
          {error || hint}
        </p>
      )}
    </div>
  );
}
