import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api } from "@/api/client";
import { cn } from "@/lib/utils";

interface LinkFieldProps {
  label: string;
  value: string;
  onChange: (value: string) => void;
  linkDoctype: string;
  readOnly: boolean;
}

export function LinkField({
  label,
  value,
  onChange,
  linkDoctype,
  readOnly,
}: LinkFieldProps) {
  const [query, setQuery] = useState(value ?? "");
  const [results, setResults] = useState<any[]>([]);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [dropdownStyle, setDropdownStyle] = useState<React.CSSProperties>({});
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const selectingRef = useRef(false);

  // Sync external value changes
  useEffect(() => {
    setQuery(value ?? "");
  }, [value]);

  const updateDropdownPosition = useCallback(() => {
    if (inputRef.current) {
      const rect = inputRef.current.getBoundingClientRect();
      setDropdownStyle({
        position: "fixed",
        top: rect.bottom + 4,
        left: rect.left,
        width: rect.width,
        zIndex: 9999,
      });
    }
  }, []);

  const search = useCallback(
    (q: string) => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
      debounceRef.current = setTimeout(async () => {
        setLoading(true);
        try {
          const data = await api.searchLink(linkDoctype, q);
          setResults(data);
          setOpen(true);
        } catch {
          setResults([]);
        } finally {
          setLoading(false);
        }
      }, 300);
    },
    [linkDoctype],
  );

  const handleInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const q = e.target.value;
    setQuery(q);
    search(q);
  };

  const handleSelect = (item: any) => {
    const name = item.name ?? item.id ?? String(item);
    setQuery(name);
    onChange(name);
    setOpen(false);
    setResults([]);
  };

  const handleBlur = () => {
    setTimeout(() => {
      if (selectingRef.current) {
        selectingRef.current = false;
        return;
      }
      setOpen(false);
      if (query !== value) {
        onChange(query);
      }
    }, 150);
  };

  // Close dropdown on outside click (check both container and portal dropdown)
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      const target = e.target as Node;
      if (containerRef.current?.contains(target)) return;
      if (dropdownRef.current?.contains(target)) return;
      setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  if (readOnly) {
    return (
      <div>
        {label && (
          <span className="mb-1.5 block text-sm font-medium text-fg">
            {label}
          </span>
        )}
        <span className="text-sm text-fg">{value || "\u2014"}</span>
      </div>
    );
  }

  return (
    <div ref={containerRef} className="relative">
      {label && (
        <label className="mb-1.5 block text-sm font-medium text-fg">
          {label}
        </label>
      )}
      <input
        ref={inputRef}
        type="text"
        value={query}
        onChange={handleInputChange}
        onFocus={() => {
          updateDropdownPosition();
          search(query);
        }}
        onBlur={handleBlur}
        placeholder={`Search ${linkDoctype}...`}
        className={cn(
          "block h-9 w-full rounded-lg bg-surface px-3 text-sm text-fg ring-1 ring-line transition-all placeholder:text-fg-muted/70 focus:outline-none focus:ring-2 focus:ring-brand/30",
          loading && "bg-surface-subtle",
        )}
      />
      {open && results.length > 0 && createPortal(
        <div ref={dropdownRef} style={dropdownStyle} className="max-h-48 overflow-y-auto rounded-lg bg-surface ring-1 ring-line shadow-card-hover">
          {results.map((item, i) => {
            const name = item.name ?? item.id ?? String(item);
            return (
              <button
                key={i}
                type="button"
                onMouseDown={() => { selectingRef.current = true; }}
                onClick={() => handleSelect(item)}
                className="block w-full px-3 py-1.5 text-left text-sm text-fg transition-colors hover:bg-surface-subtle"
              >
                {name}
              </button>
            );
          })}
        </div>,
        document.body,
      )}
    </div>
  );
}
