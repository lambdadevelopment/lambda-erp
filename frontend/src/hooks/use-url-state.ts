import { useCallback } from "react";
import { useSearchParams } from "react-router-dom";

/**
 * Atomic multi-param updater. Works around a react-router-dom quirk:
 * calling several `useUrlState` setters in the same handler races, because
 * each functional updater reads the same cached `prev` and the last call
 * wins — silently dropping the other updates. Pass all changes in one call
 * so they land in a single navigate.
 *
 * `null`, `undefined`, or empty string remove the param from the URL.
 */
export function useUrlPatch() {
  const [, setParams] = useSearchParams();
  return useCallback(
    (patch: Record<string, string | number | null | undefined>) => {
      setParams(
        (prev) => {
          const p = new URLSearchParams(prev);
          for (const [key, value] of Object.entries(patch)) {
            if (value === "" || value == null) {
              p.delete(key);
            } else {
              p.set(key, String(value));
            }
          }
          return p;
        },
        { replace: true },
      );
    },
    [setParams],
  );
}

/**
 * A URL-backed useState for a single query param.
 *
 * - Initial value is read from the URL; falls back to `defaultValue`.
 * - Updates use `{ replace: true }` so filter/pagination tweaks don't pollute
 *   browser history — the Back button still takes the user to where they came
 *   from, not through each intermediate filter state.
 * - Values equal to `defaultValue` (and empty strings) are stripped from the
 *   URL so a freshly-loaded page doesn't carry a bunch of `?status=All&page=1`
 *   noise.
 * - Coerces to number when `defaultValue` is a number.
 */
export function useUrlState<T extends string | number>(
  key: string,
  defaultValue: T,
): [T, (next: T) => void] {
  const [params, setParams] = useSearchParams();
  const raw = params.get(key);

  let value: T;
  if (raw === null) {
    value = defaultValue;
  } else if (typeof defaultValue === "number") {
    const parsed = Number(raw);
    value = (Number.isFinite(parsed) ? parsed : defaultValue) as T;
  } else {
    value = raw as T;
  }

  const setValue = useCallback(
    (next: T) => {
      setParams(
        (prev) => {
          const p = new URLSearchParams(prev);
          if (next === defaultValue || next === "" || next == null) {
            p.delete(key);
          } else {
            p.set(key, String(next));
          }
          return p;
        },
        { replace: true },
      );
    },
    [key, defaultValue, setParams],
  );

  return [value, setValue];
}
