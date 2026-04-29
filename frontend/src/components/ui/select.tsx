import { cn } from "@/lib/utils";

// Either a plain string (shown as both label + value) or an object form for
// nicer labels (e.g. Yes/No on top of a boolean 1/0 value).
export type SelectOption = string | { value: string; label: string };

interface SelectProps {
  label?: string;
  options: SelectOption[];
  value?: string;
  onChange?: React.ChangeEventHandler<HTMLSelectElement>;
  className?: string;
  required?: boolean;
  disabled?: boolean;
}

function normalizeOption(opt: SelectOption): { value: string; label: string } {
  return typeof opt === "string" ? { value: opt, label: opt } : opt;
}

export function Select({ label, options, value, onChange, className, required, disabled }: SelectProps) {
  const selectId = label ? label.toLowerCase().replace(/\s+/g, "-") : undefined;

  return (
    <div>
      {label && (
        <label
          htmlFor={selectId}
          className="mb-1.5 block text-sm font-medium text-fg"
        >
          {label}
        </label>
      )}
      <select
        id={selectId}
        value={value}
        onChange={onChange}
        required={required}
        disabled={disabled}
        className={cn(
          // Same shape as the Input primitive: h-10, surface bg, line ring,
          // brand-tinted halo on focus.
          "block h-10 w-full rounded-lg bg-surface px-3 text-sm text-fg",
          "ring-1 ring-line transition-all",
          "focus:outline-none focus:ring-2 focus:ring-brand/30",
          "disabled:cursor-not-allowed disabled:bg-surface-subtle disabled:text-fg-muted",
          className,
        )}
      >
        <option value="">Select...</option>
        {options.map((opt) => {
          const { value: v, label: l } = normalizeOption(opt);
          return (
            <option key={v} value={v}>
              {l}
            </option>
          );
        })}
      </select>
    </div>
  );
}
