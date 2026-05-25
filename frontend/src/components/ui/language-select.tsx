import { useTranslation } from "react-i18next";
import { Select } from "@/components/ui/select";
import { SUPPORTED_LANGUAGES } from "@/i18n";

/**
 * Language dropdown. Reads the active language from i18next and switches it on
 * change; the i18n layer persists the choice to localStorage. `label` defaults
 * to the translated "Language" string but can be suppressed (e.g. in a header).
 */
export function LanguageSelect({ label }: { label?: string | null }) {
  const { t, i18n } = useTranslation();
  // i18n.language can carry a region suffix (e.g. "en-US"); match on the base.
  const current = i18n.language?.split("-")[0] ?? "en";

  return (
    <Select
      label={label === null ? undefined : label ?? t("language.label")}
      options={SUPPORTED_LANGUAGES.map((l) => ({ value: l.code, label: l.label }))}
      value={current}
      onChange={(e) => i18n.changeLanguage(e.target.value)}
    />
  );
}
