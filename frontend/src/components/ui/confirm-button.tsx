// Inline arm-to-confirm button for low-stakes, in-row actions (e.g. removing a
// row from a list). On first click it splits in place into "Cancel | Confirm",
// with **Cancel occupying the trigger's original position** — so an accidental
// double-click's second click lands on Cancel and nothing happens. It
// auto-collapses after `timeout` ms or on pointer-leave.
//
// For heavier or destructive actions (deleting a whole record), prefer the
// modal useConfirm() from dialog.tsx instead — a transient inline split in a
// busy toolbar is more clutter than a focused dialog.
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";

interface ConfirmButtonProps {
  onConfirm: () => void;
  /** Resting trigger content (icon or text). */
  children: React.ReactNode;
  confirmLabel?: React.ReactNode;
  cancelLabel?: React.ReactNode;
  title?: string; // tooltip on the resting trigger
  danger?: boolean; // red confirm (default true)
  disabled?: boolean;
  className?: string; // applied to the resting trigger
  timeout?: number; // auto-collapse ms (default 4000)
}

export function ConfirmButton({
  onConfirm,
  children,
  confirmLabel,
  cancelLabel,
  title,
  danger = true,
  disabled,
  className,
  timeout = 4000,
}: ConfirmButtonProps) {
  const { t } = useTranslation();
  const [armed, setArmed] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  useEffect(() => {
    if (!armed) return;
    timer.current = setTimeout(() => setArmed(false), timeout);
    return () => clearTimeout(timer.current);
  }, [armed, timeout]);

  if (!armed) {
    return (
      <button
        type="button"
        disabled={disabled}
        title={title}
        onClick={() => setArmed(true)}
        className={className}
      >
        {children}
      </button>
    );
  }

  return (
    <span
      className="inline-flex items-center gap-1"
      onMouseLeave={() => setArmed(false)}
    >
      {/* Cancel sits where the trigger was, so a double-click cancels safely. */}
      <button
        type="button"
        onClick={() => setArmed(false)}
        className="rounded-md px-2 py-1 text-xs text-fg-muted hover:text-fg"
      >
        {cancelLabel ?? t("common.cancel", { defaultValue: "Cancel" })}
      </button>
      <button
        type="button"
        onClick={() => {
          setArmed(false);
          onConfirm();
        }}
        className={cn(
          "rounded-md px-2 py-1 text-xs font-medium text-white",
          danger ? "bg-red-600 hover:bg-red-700" : "bg-brand hover:bg-brand/90",
        )}
      >
        {confirmLabel ?? t("common.delete", { defaultValue: "Delete" })}
      </button>
    </span>
  );
}
