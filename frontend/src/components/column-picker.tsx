// The "Columns" menu of a list: the shown columns on top in display order,
// dragged into place by their handle (mouse, touch, or the arrow keys on a
// focused handle); the hidden ones below a separator, alphabetical, with a
// search field. Switching a column on appends it to the shown ones, switching
// it off moves it down. A pinned column (a document list's `name`, the link of
// every row) stays first and shown. Since 1.4.30.
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { GripVertical, Lock, Search, SlidersHorizontal } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Sortable, type SortableHandleProps } from "@/components/ui/sortable";

export interface PickerColumn {
  name: string;
  label: string;
}

/** The shown columns with the pinned one first, whatever order they were saved in. */
export function withPinnedFirst(columns: string[], pinned?: string): string[] {
  if (!pinned) return columns;
  return [pinned, ...columns.filter((c) => c !== pinned)];
}

function Handle({ label, handle }: { label: string; handle: SortableHandleProps }) {
  const { t } = useTranslation();
  const text = t("menuLayout.drag", { name: label });
  return (
    <button type="button" {...handle} aria-label={text} title={text}
      className="rounded p-0.5 text-fg-muted transition-colors hover:bg-surface-subtle hover:text-fg focus:outline-none focus-visible:ring-2 focus-visible:ring-brand/40">
      <GripVertical className="h-4 w-4" />
    </button>
  );
}

export function ColumnPicker({ available, shown, pinned, onChange, onReset }: {
  available: PickerColumn[];
  /** The shown columns in display order. */
  shown: string[];
  /** A column that is always shown and first (not movable, not hideable). */
  pinned?: string;
  /** The new shown columns in display order. */
  onChange: (columns: string[]) => void;
  /** Back to the list's default columns. */
  onReset?: () => void;
}) {
  const { t, i18n } = useTranslation();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);
  useEffect(() => { if (!open) setQuery(""); }, [open]);

  const byName = useMemo(() => new Map(available.map((c) => [c.name, c])), [available]);
  // A shown column the list does not offer by name (a default such as
  // `status`) is still listed and kept, so a change never drops it silently.
  const label = (name: string) => {
    const known = byName.get(name)?.label;
    if (known) return known;
    const titled = name.split("_").map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(" ");
    return t(`fields.${titled}`, { defaultValue: titled });
  };
  const pin = pinned && (byName.has(pinned) || shown.includes(pinned)) ? pinned : undefined;
  // Shown columns, pinned first; the movable ones after it.
  const shownCols = withPinnedFirst(shown, pin);
  const movable = shownCols.filter((c) => c !== pin);
  const needle = query.trim().toLocaleLowerCase();
  const matches = (c: PickerColumn) => !needle || c.label.toLocaleLowerCase().includes(needle) || c.name.toLowerCase().includes(needle);
  const shownMatches = shownCols.filter((name) => matches({ name, label: label(name) }));
  const hidden = available
    .filter((c) => !shownCols.includes(c.name) && matches(c))
    .sort((a, b) => a.label.localeCompare(b.label, i18n.language));

  const save = (columns: string[]) => {
    const next = withPinnedFirst(columns, pin);
    if (next.length) onChange(next); // never leave an empty table
  };
  const lastOne = shownCols.length === 1;

  const checkbox = (name: string, checked: boolean, disabled: boolean) => (
    <input
      type="checkbox"
      className="h-4 w-4 shrink-0 rounded border-line text-brand focus:ring-brand/30 disabled:opacity-50"
      checked={checked}
      disabled={disabled}
      aria-label={label(name)}
      onChange={() => save(checked ? shownCols.filter((c) => c !== name) : [...shownCols, name])}
    />
  );
  const section = (text: string) => (
    <div className="px-2 pb-1 pt-2 text-xs font-medium uppercase tracking-wide text-fg-muted">{text}</div>
  );

  return (
    <div className="relative" ref={ref}>
      <Button variant="secondary" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        <span className="inline-flex items-center gap-1.5">
          <SlidersHorizontal className="h-4 w-4" />
          {t("common.columns", { defaultValue: "Columns" })}
        </span>
      </Button>
      {open && (
        <div className="absolute right-0 z-20 mt-2 flex max-h-[28rem] w-72 flex-col rounded-lg border border-line bg-surface shadow-card">
          <div className="border-b border-line p-2">
            <div className="relative">
              <Search className="pointer-events-none absolute left-2 top-1/2 h-4 w-4 -translate-y-1/2 text-fg-muted" />
              <input
                type="search"
                // Not on touch screens: the keyboard would cover the menu.
                autoFocus={typeof window !== "undefined" && !window.matchMedia?.("(pointer: coarse)").matches}
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder={t("columnPicker.search", { defaultValue: "Find a column" })}
                className="w-full rounded-md border border-line bg-surface py-1.5 pl-8 pr-2 text-sm text-fg placeholder:text-fg-muted focus:border-brand focus:outline-none focus:ring-2 focus:ring-brand/20"
              />
            </div>
          </div>
          <div className="overflow-y-auto p-2">
            {!needle && (
              <>
                {section(t("columnPicker.shown", { defaultValue: "Shown" }))}
                {pin && shownCols[0] === pin && (
                  <div className="flex items-center gap-2 rounded px-2 py-1.5 text-sm text-fg"
                    title={t("columnPicker.pinned", { defaultValue: "Always shown first: it links to the record" })}>
                    <Lock className="h-4 w-4 shrink-0 p-0.5 text-fg-muted" />
                    {checkbox(pin, true, true)}
                    <span className="truncate">{label(pin)}</span>
                  </div>
                )}
                <Sortable items={movable} keyOf={(c) => c} onReorder={(next) => save(next)}>
                  {(name, { handle, dragging }) => (
                    <div className={`flex items-center gap-2 rounded px-2 py-1.5 text-sm text-fg ${dragging ? "bg-brand/10 shadow-sm" : "hover:bg-surface-subtle"}`}>
                      <Handle label={label(name)} handle={handle} />
                      {checkbox(name, true, lastOne)}
                      <span className="truncate">{label(name)}</span>
                    </div>
                  )}
                </Sortable>
              </>
            )}
            {needle && shownMatches.length > 0 && (
              <>
                {section(t("columnPicker.shown", { defaultValue: "Shown" }))}
                {shownMatches.map((name) => (
                  <label key={name} className="flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-sm text-fg hover:bg-surface-subtle">
                    <span className="w-5 shrink-0" />
                    {checkbox(name, true, lastOne || name === pin)}
                    <span className="truncate">{label(name)}</span>
                  </label>
                ))}
              </>
            )}
            {hidden.length > 0 && (
              <>
                <div className="my-1 border-t border-line" />
                {section(t("columnPicker.hidden", { defaultValue: "Hidden" }))}
                {hidden.map((c) => (
                  <label key={c.name} className="flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-sm text-fg hover:bg-surface-subtle">
                    <span className="w-5 shrink-0" />
                    {checkbox(c.name, false, false)}
                    <span className="truncate">{c.label}</span>
                  </label>
                ))}
              </>
            )}
            {needle && hidden.length === 0 && shownMatches.length === 0 && (
              <div className="px-2 py-3 text-sm text-fg-muted">
                {t("columnPicker.none", { defaultValue: "No column matches." })}
              </div>
            )}
          </div>
          {onReset && (
            <div className="border-t border-line p-2 text-right">
              <button type="button" onClick={onReset}
                className="rounded px-2 py-1 text-xs text-fg-muted hover:bg-surface-subtle hover:text-fg">
                {t("columnPicker.reset", { defaultValue: "Reset to default" })}
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
