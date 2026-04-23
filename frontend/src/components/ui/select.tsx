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
          className="mb-1 block text-sm font-medium text-gray-700"
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
          "block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 disabled:cursor-not-allowed disabled:bg-gray-50",
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
