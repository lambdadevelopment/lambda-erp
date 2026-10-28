// Save button that reflects the form's state instead of flashing on click:
//   unsaved changes → "Save" (primary)
//   saving          → spinner + "Saving…"
//   nothing changed → ✓ "Saved" (quiet, disabled) until the next edit
// A new, never-saved record always offers "Save". A failed save leaves the
// form dirty, so the button returns to "Save" next to the page's error.
import { Check, Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";

export function SaveButton({
  dirty,
  saving,
  isNew = false,
  disabled = false,
  onClick,
}: {
  dirty: boolean;
  saving: boolean;
  /** A record that doesn't exist yet can always be saved. */
  isNew?: boolean;
  /** Extra reasons the save can't run (e.g. missing required fields). */
  disabled?: boolean;
  onClick: () => void;
}) {
  const { t } = useTranslation();

  if (saving) {
    return (
      <Button disabled>
        <Loader2 className="h-4 w-4 animate-spin" />
        {t("common.saving")}
      </Button>
    );
  }
  if (!isNew && !dirty) {
    // Not an action, so a status with the button's footprint rather than a
    // disabled button (which would look broken at half opacity).
    return (
      <span
        role="status"
        className="inline-flex h-10 items-center justify-center gap-2 rounded-lg bg-surface px-4 text-sm font-medium text-fg-muted ring-1 ring-line"
      >
        <Check className="h-4 w-4 text-emerald-600" />
        {t("common.saved")}
      </span>
    );
  }
  return (
    <Button onClick={onClick} disabled={disabled}>
      {t("common.save")}
    </Button>
  );
}
