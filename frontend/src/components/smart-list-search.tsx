import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Filter, Search, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const CONTAINS_SUFFIX = "__contains";

export interface SmartSearchOption {
  value: string;
  label: string;
}

export interface SmartSearchField {
  name: string;
  label: string;
  type?: string;
  options?: SmartSearchOption[];
  /** The backend schema confirms that this column supports __contains. */
  contains?: boolean;
  /** Low-cardinality fields may suggest values before the user types a prefix. */
  suggestOnEmpty?: boolean;
}

interface QualifierToken {
  field: SmartSearchField;
  value: string;
  start: number;
}

interface SmartListSearchProps {
  scope: string;
  value: string;
  onChange: (value: string) => void;
  fields: SmartSearchField[];
  filters: Record<string, string>;
  onFiltersChange: (updates: Record<string, string | null>) => void;
  onSubmit: (search: string, updates: Record<string, string | null>) => void;
  loadValues: (field: string, prefix: string) => Promise<Array<string | number>>;
  hiddenChipFields?: string[];
  className?: string;
}

function currentQualifier(value: string, fields: SmartSearchField[]): QualifierToken | null {
  // The unfinished quoted form deliberately matches to end-of-input, so
  // `town:"St. G` can still drive suggestions before the closing quote exists.
  const match = /(?:^|\s)([A-Za-z_][\w-]*):(?:"([^"]*)"?|([^\s]*))$/.exec(value);
  if (!match) return null;
  const field = fields.find((candidate) => candidate.name === match[1]);
  if (!field) return null;
  const leadingSpace = match[0].startsWith(" ") ? 1 : 0;
  return {
    field,
    value: match[2] ?? match[3] ?? "",
    start: (match.index ?? 0) + leadingSpace,
  };
}

function withoutToken(value: string, token: QualifierToken): string {
  // A qualifier is only recognized at the end of the draft, so dropping the
  // suffix also handles quoted values containing spaces without token surgery.
  return value.slice(0, token.start).trim().replace(/\s+/g, " ");
}

function filterKey(field: SmartSearchField): string {
  return field.contains ? `${field.name}${CONTAINS_SUFFIX}` : field.name;
}

function alternateFilterKey(field: SmartSearchField): string {
  return field.contains ? field.name : `${field.name}${CONTAINS_SUFFIX}`;
}

function extractQualifiers(value: string, fields: SmartSearchField[]) {
  const known = new Map(fields.map((field) => [field.name, field]));
  const updates: Record<string, string | null> = {};
  const spans: Array<{ start: number; end: number }> = [];
  const pattern = /(?:^|\s)([A-Za-z_][\w-]*):(?:"([^"]*)"|([^\s]+))/g;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(value)) !== null) {
    const field = known.get(match[1]);
    if (!field) continue;
    const selectedValue = match[2] ?? match[3] ?? "";
    if (!selectedValue) continue;
    const leadingSpace = match[0].startsWith(" ") ? 1 : 0;
    updates[filterKey(field)] = selectedValue;
    updates[alternateFilterKey(field)] = null;
    spans.push({ start: (match.index ?? 0) + leadingSpace, end: pattern.lastIndex });
  }
  let text = value;
  for (const span of spans.reverse()) text = `${text.slice(0, span.start)}${text.slice(span.end)}`;
  return { updates, text: text.trim().replace(/\s+/g, " ") };
}

function optionLabel(option: SmartSearchOption | string | number): string {
  if (typeof option === "object") return option.label;
  return String(option);
}

function optionValue(option: SmartSearchOption | string | number): string {
  if (typeof option === "object") return option.value;
  return String(option);
}

export function SmartListSearch({
  scope,
  value,
  onChange,
  fields,
  filters,
  onFiltersChange,
  onSubmit,
  loadValues,
  hiddenChipFields = [],
  className,
}: SmartListSearchProps) {
  const { t } = useTranslation();
  const rootRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const [open, setOpen] = useState(false);
  const [appendMode, setAppendMode] = useState(false);
  const [debouncedQualifier, setDebouncedQualifier] = useState({ field: "", value: "" });

  const qualifier = useMemo(() => currentQualifier(value, fields), [value, fields]);
  const lastToken = value.trimEnd().split(/\s+/).pop() ?? "";
  const fieldQuery = appendMode || qualifier || lastToken.includes(":") ? "" : lastToken.toLowerCase();
  const matchingFields = useMemo(() => {
    const ranked = fields.filter((field) =>
      !fieldQuery || field.name.toLowerCase().includes(fieldQuery) || field.label.toLowerCase().includes(fieldQuery)
    );
    return ranked.sort((a, b) => {
      const aSelected = filters[a.name] || filters[`${a.name}${CONTAINS_SUFFIX}`] ? 1 : 0;
      const bSelected = filters[b.name] || filters[`${b.name}${CONTAINS_SUFFIX}`] ? 1 : 0;
      if (aSelected !== bSelected) return bSelected - aSelected;
      const aSuggested = a.suggestOnEmpty ? 1 : 0;
      const bSuggested = b.suggestOnEmpty ? 1 : 0;
      if (aSuggested !== bSuggested) return bSuggested - aSuggested;
      return a.label.localeCompare(b.label);
    });
  }, [fields, fieldQuery, filters]);

  const staticOptions = qualifier?.field.options ?? [];
  useEffect(() => {
    const timer = setTimeout(() => setDebouncedQualifier({
      field: qualifier?.field.name ?? "",
      value: qualifier?.value ?? "",
    }), 200);
    return () => clearTimeout(timer);
  }, [qualifier?.field.name, qualifier?.value]);
  const qualifierIsDebounced = !!qualifier && debouncedQualifier.field === qualifier.field.name;
  const shouldLoadValues = qualifierIsDebounced && staticOptions.length === 0 &&
    (!!debouncedQualifier.value || !!qualifier!.field.suggestOnEmpty);
  const { data: loadedValues = [], isFetching } = useQuery({
    queryKey: ["smart-list-values", scope, qualifier?.field.name, debouncedQualifier.value],
    queryFn: () => loadValues(qualifier!.field.name, debouncedQualifier.value),
    enabled: shouldLoadValues,
    staleTime: 60_000,
  });
  const valueOptions: Array<SmartSearchOption | string | number> = staticOptions.length
    ? staticOptions.filter((option) =>
        !qualifier?.value || option.label.toLowerCase().includes(qualifier.value.toLowerCase()) ||
        option.value.toLowerCase().startsWith(qualifier.value.toLowerCase())
      )
    : loadedValues;

  useEffect(() => {
    const onPointerDown = (event: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) {
        setOpen(false);
        setAppendMode(false);
      }
    };
    document.addEventListener("mousedown", onPointerDown);
    return () => document.removeEventListener("mousedown", onPointerDown);
  }, []);

  const insertField = (field: SmartSearchField) => {
    const trimmed = value.trimEnd();
    let next: string;
    if (!appendMode && fieldQuery && !lastToken.includes(":")) {
      const tokenStart = trimmed.lastIndexOf(lastToken);
      next = `${trimmed.slice(0, tokenStart)}${field.name}:`;
    } else {
      next = `${trimmed}${trimmed ? " " : ""}${field.name}:`;
    }
    onChange(next);
    setAppendMode(false);
    setOpen(true);
    requestAnimationFrame(() => inputRef.current?.focus());
  };

  const commitQualifier = (selectedValue: string) => {
    if (!qualifier || !selectedValue) return;
    onFiltersChange({
      [filterKey(qualifier.field)]: selectedValue,
      [alternateFilterKey(qualifier.field)]: null,
    });
    onChange(withoutToken(value, qualifier));
    setAppendMode(false);
    setOpen(false);
    requestAnimationFrame(() => inputRef.current?.focus());
  };

  const showFields = () => {
    setAppendMode(true);
    setOpen(true);
    requestAnimationFrame(() => inputRef.current?.focus());
  };

  const submitSearch = () => {
    const parsed = extractQualifiers(value, fields);
    onSubmit(parsed.text, parsed.updates);
    if (parsed.text !== value) onChange(parsed.text);
    setAppendMode(false);
    setOpen(false);
  };

  const visibleChips = Object.entries(filters)
    .filter(([, filterValue]) => filterValue !== "")
    .map(([key, filterValue]) => {
      const fieldName = key.endsWith(CONTAINS_SUFFIX) ? key.slice(0, -CONTAINS_SUFFIX.length) : key;
      return { key, fieldName, filterValue };
    })
    .filter(({ fieldName }) => !hiddenChipFields.includes(fieldName))
    .map(({ key, fieldName, filterValue }) => ({
      key,
      field: fields.find((candidate) => candidate.name === fieldName),
      value: filterValue,
    }))
    .filter((entry) => !!entry.field);

  return (
    <div ref={rootRef} className={cn("relative flex w-full flex-wrap items-center gap-2 sm:w-auto", className)}>
      <div className="relative w-full sm:w-[32rem] lg:w-[38rem]">
        <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-fg-muted" />
        <input
          ref={inputRef}
          type="search"
          role="combobox"
          aria-expanded={open}
          aria-controls="smart-list-search-options"
          value={value}
          onFocus={() => setOpen(true)}
          onChange={(event) => {
            onChange(event.target.value);
            setAppendMode(false);
            setOpen(true);
          }}
          onKeyDown={(event) => {
            if (event.key === "Escape") {
              setOpen(false);
              setAppendMode(false);
            } else if (event.key === "Enter") {
              event.preventDefault();
              submitSearch();
            }
          }}
          placeholder={t("smartSearch.placeholder")}
          className="h-8 w-full rounded-md bg-surface pl-8 pr-3 text-sm text-fg ring-1 ring-line placeholder:text-fg-muted/70 focus:outline-none focus:ring-2 focus:ring-brand/30"
        />
      </div>

      <Button size="sm" onClick={submitSearch}>
        <Search className="h-4 w-4" />
        {t("smartSearch.search")}
      </Button>

      <Button size="sm" variant="secondary" onClick={showFields}>
        <Filter className="h-4 w-4" />
        {t("smartSearch.addFilter")}
      </Button>

      {visibleChips.map(({ key, field, value: filterValue }) => (
        <button
          key={key}
          type="button"
          onClick={() => onFiltersChange({ [key]: null })}
          title={t("smartSearch.removeFilter", { label: field!.label })}
          className="inline-flex h-8 items-center gap-1.5 rounded-full bg-brand/10 px-3 text-sm font-medium text-brand hover:bg-brand/15"
        >
          <span>{field!.label}: {filterValue}</span>
          <X className="h-3.5 w-3.5" />
        </button>
      ))}

      {open && (
        <div
          id="smart-list-search-options"
          role="listbox"
          className="absolute left-0 top-full z-30 mt-2 max-h-80 w-[min(32rem,calc(100vw-2rem))] overflow-y-auto rounded-lg border border-line bg-surface p-2 shadow-card"
        >
          {qualifier ? (
            <>
              <div className="flex items-center justify-between border-b border-line px-2 pb-2 pt-1">
                <div>
                  <div className="text-sm font-medium text-fg">
                    {t("smartSearch.valueFor", { label: qualifier.field.label })}
                  </div>
                  <code className="text-xs text-fg-muted">{qualifier.field.name}:</code>
                </div>
                <button
                  type="button"
                  className="text-xs font-medium text-brand hover:underline"
                  onClick={() => {
                    onChange(value.slice(0, qualifier.start).trimEnd());
                    setAppendMode(true);
                  }}
                >
                  {t("smartSearch.allFields")}
                </button>
              </div>
              {!qualifier.value && !qualifier.field.suggestOnEmpty && staticOptions.length === 0 && (
                <div className="px-3 py-4 text-sm text-fg-muted">{t("smartSearch.typeForValues")}</div>
              )}
              {isFetching && (
                <div className="px-3 py-3 text-sm text-fg-muted">{t("common.loading")}</div>
              )}
              {!isFetching && valueOptions.map((option) => (
                <button
                  key={optionValue(option)}
                  type="button"
                  role="option"
                  className="flex w-full items-center justify-between rounded-md px-3 py-2 text-left text-sm hover:bg-surface-subtle"
                  onClick={() => commitQualifier(optionValue(option))}
                >
                  <span className="text-fg">{optionLabel(option)}</span>
                  {optionLabel(option) !== optionValue(option) && (
                    <code className="ml-4 text-xs text-fg-muted">{optionValue(option)}</code>
                  )}
                </button>
              ))}
              {!isFetching && shouldLoadValues && valueOptions.length === 0 && (
                <div className="px-3 py-4 text-sm text-fg-muted">{t("smartSearch.noValues")}</div>
              )}
              {!!qualifier.value && (
                <button
                  type="button"
                  className="mt-1 w-full rounded-md border-t border-line px-3 py-2 text-left text-sm text-brand hover:bg-surface-subtle"
                  onClick={() => commitQualifier(qualifier.value)}
                >
                  {t("smartSearch.useValue", { value: qualifier.value })}
                </button>
              )}
            </>
          ) : (
            <>
              <div className="border-b border-line px-2 pb-2 pt-1">
                <div className="text-sm font-medium text-fg">{t("smartSearch.availableFields")}</div>
                <div className="text-xs text-fg-muted">{t("smartSearch.fieldHelp")}</div>
              </div>
              {matchingFields.map((field) => (
                <button
                  key={field.name}
                  type="button"
                  role="option"
                  className="flex w-full items-center justify-between gap-4 rounded-md px-3 py-2 text-left hover:bg-surface-subtle"
                  onClick={() => insertField(field)}
                >
                  <span>
                    <span className="block text-sm text-fg">{field.label}</span>
                    <span className="block text-xs text-fg-muted">
                      {field.type
                        ? t(`smartSearch.types.${field.type}`, { defaultValue: field.type })
                        : t("smartSearch.field")}
                    </span>
                  </span>
                  <span className="text-right">
                    <code className="block text-xs text-fg-muted">{field.name}:</code>
                    {filters[field.name] && (
                      <span className="block max-w-48 truncate text-xs text-brand">{filters[field.name]}</span>
                    )}
                  </span>
                </button>
              ))}
              {matchingFields.length === 0 && (
                <div className="px-3 py-4 text-sm text-fg-muted">{t("smartSearch.noFields")}</div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}
