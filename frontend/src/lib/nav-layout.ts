/**
 * Personal sidebar layout (api/menu_layout.py): each user can hide and reorder
 * menu groups and items. The layout lives in the `nav_layout` preference; the
 * menu this user sees is published as `nav_catalog`, so the chat, REST and MCP
 * tools can name and validate entries without knowing the frontend registry.
 */
import type { NavGroup } from "@/lib/nav";

export interface NavLayout {
  order: string[];                  // group keys to put first, in that order
  items: Record<string, string[]>;  // group key -> item paths in that order
  hidden: string[];                 // group keys and item paths
}

export interface CatalogGroup {
  key: string; label: string; title: string;
  items: { key: string; label: string; title: string }[];
}

/** Sidebar groups that are components rather than registry entries. */
export const BUILTIN_GROUPS = ["Chats", "Custom Analytics"] as const;

/** The page that edits the menu: never hideable (nor is its group). */
export const MENU_SETTINGS_PATH = "/settings/menu";

export const EMPTY_LAYOUT: NavLayout = { order: [], items: {}, hidden: [] };

export function parseLayout(raw: string | undefined): NavLayout {
  try {
    const value = raw ? JSON.parse(raw) : {};
    return {
      order: Array.isArray(value.order) ? value.order : [],
      items: value.items && typeof value.items === "object" ? value.items : {},
      hidden: Array.isArray(value.hidden) ? value.hidden : [],
    };
  } catch {
    return EMPTY_LAYOUT;
  }
}

/** Keys named in `preferred` first, in that order; the rest keep their default order. */
export function ordered<T>(entries: T[], key: (entry: T) => string, preferred: string[] = []): T[] {
  const byKey = new Map(entries.map((e) => [key(e), e]));
  const first = [...new Set(preferred)].filter((k) => byKey.has(k));
  return [...first.map((k) => byKey.get(k)!), ...entries.filter((e) => !first.includes(key(e)))];
}

/** The groups a user can see, as the catalog the server validates against. */
export function buildCatalog(groups: NavGroup[], t: (key: string, opts: { defaultValue: string }) => string): CatalogGroup[] {
  return [
    ...BUILTIN_GROUPS.map((label) => ({
      key: label, label,
      title: t(label === "Chats" ? "nav.chats" : "nav.customAnalytics", { defaultValue: label }),
      items: [],
    })),
    ...groups.map((group) => ({
      key: group.label, label: group.label,
      title: t(`nav.groups.${group.label}`, { defaultValue: group.label }),
      items: group.items.map((item) => ({
        key: item.path, label: item.label,
        title: t(`nav.items.${item.label}`, { defaultValue: item.label }),
      })),
    })),
  ];
}

export function isLocked(groupItems: { key: string }[]) {
  return groupItems.some((item) => item.key === MENU_SETTINGS_PATH);
}
