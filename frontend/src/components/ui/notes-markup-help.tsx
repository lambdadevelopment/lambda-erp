import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Info } from "lucide-react";

// Info icon next to the Notes / Terms label that explains the light markup the
// field supports on the PDF (see api/remarks_md.py in the core). Opens on hover
// or click; the handlers sit on the wrapper so the popover stays open while the
// pointer moves into it to read.
export function NotesMarkupHelp() {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);

  const rows: [string, string][] = [
    ["# Heading", t("notesMarkup.heading", { defaultValue: "bold heading" })],
    ["*italic*  **bold**", t("notesMarkup.emphasis", { defaultValue: "italic / bold" })],
    ["---", t("notesMarkup.rule", { defaultValue: "horizontal divider line" })],
    [
      ">> Period | Amount",
      t("notesMarkup.price", {
        defaultValue: "right-aligned price beside the text above (e.g. Monthly | CHF 380.—)",
      }),
    ],
  ];

  return (
    <span
      className="relative ml-1 inline-flex align-middle"
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
    >
      <button
        type="button"
        className="inline-flex h-4 w-4 items-center justify-center rounded-full text-fg-muted hover:text-fg"
        onClick={() => setOpen((v) => !v)}
        aria-label={t("notesMarkup.title", { defaultValue: "Formatting help" })}
      >
        <Info className="h-3.5 w-3.5" />
      </button>
      {open && (
        <div className="absolute bottom-full left-0 z-50 mb-2 w-80 rounded-md bg-gray-900 px-3 py-2.5 text-xs leading-relaxed text-white shadow-lg">
          <p className="mb-2 text-gray-200">
            {t("notesMarkup.intro", {
              defaultValue: "The Notes / Terms text supports light formatting on the PDF:",
            })}
          </p>
          <table className="w-full border-separate border-spacing-y-1">
            <tbody>
              {rows.map(([syntax, desc]) => (
                <tr key={syntax}>
                  <td className="pr-3 align-top font-mono text-[11px] text-emerald-300">{syntax}</td>
                  <td className="align-top text-gray-200">{desc}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="mt-2 text-gray-400">
            {t("notesMarkup.block", { defaultValue: "Separate blocks with a blank line." })}
          </p>
        </div>
      )}
    </span>
  );
}
