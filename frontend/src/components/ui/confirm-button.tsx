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
  /** Armed-button size: "sm" (compact, for icon triggers — default) or "md"
   * (normal button height, to match a full-size labelled trigger). */
  size?: "sm" | "md";
  /** Which side the Cancel button sits on when armed. It should land where the
   * trigger was so a double-click cancels: "left" for a left-aligned trigger
   * (default), "right" for a right-aligned one (e.g. a toolbar delete button). */
  cancelSide?: "left" | "right";
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
  size = "sm",
  cancelSide = "left",
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

  const md = size === "md";
  const confirmBtn = (
    <button
      key="confirm"
      type="button"
      onClick={() => {
        setArmed(false);
        onConfirm();
      }}
      className={cn(
        "rounded-md font-medium text-white",
        md ? "px-3 py-1.5 text-sm" : "px-2 py-1 text-xs",
        danger ? "bg-red-600 hover:bg-red-700" : "bg-brand hover:bg-brand/90",
      )}
    >
      {confirmLabel ?? t("common.delete", { defaultValue: "Delete" })}
    </button>
  );
  const cancelBtn = (
    <button
      key="cancel"
      type="button"
      onClick={() => setArmed(false)}
      className={cn(
        "rounded-md",
        md
          ? "px-3 py-1.5 text-sm font-medium text-fg ring-1 ring-line hover:bg-surface-subtle"
          : "px-2 py-1 text-xs text-fg-muted hover:text-fg",
      )}
    >
      {cancelLabel ?? t("common.cancel", { defaultValue: "Cancel" })}
    </button>
  );

  return (
    <span
      className={cn("inline-flex items-center", md ? "gap-2" : "gap-1")}
      onMouseLeave={() => setArmed(false)}
    >
      {/* Cancel sits at the trigger's original position (cancelSide), so a
          double-click lands on Cancel and nothing happens. */}
      {cancelSide === "right" ? [confirmBtn, cancelBtn] : [cancelBtn, confirmBtn]}
    </span>
  );
}
