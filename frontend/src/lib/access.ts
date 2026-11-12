/**
 * What the signed-in user may open, for a tidy interface.
 *
 * A user with an access profile or a record scope (see docs/access-profiles.md)
 * cannot open some document types, masters and reports. The server refuses
 * those whatever the client shows; this only lets a page or a plugin leave
 * out links, buttons and menu entries that would lead to a refusal.
 */
import { useMemo } from "react";
import { useAuth, type User } from "@/contexts/auth-context";

export interface Access {
  /** True when the user has an access profile or a record scope. */
  restricted: boolean;
  /** A document or master type by its slug, e.g. "purchase-order", "supplier". */
  canOpenType: (slug: string) => boolean;
  /** A report by its name, e.g. "general-ledger" or a registered report. */
  canOpenReport: (name: string) => boolean;
  /** The kinds of records the user is restricted to ({customer: [...], warehouse: [...]}). */
  recordScope: Record<string, string[]>;
}

export function accessOf(restrictions: User["restrictions"]): Access {
  const unreadable = new Set(restrictions?.unreadable_types ?? []);
  const denied = new Set(restrictions?.denied_reports ?? []);
  return {
    restricted: !!restrictions,
    canOpenType: (slug) => !unreadable.has(slug),
    canOpenReport: (name) => !denied.has(name),
    recordScope: restrictions?.record_scope ?? {},
  };
}

export function useAccess(): Access {
  const { user } = useAuth();
  return useMemo(() => accessOf(user?.restrictions), [user?.restrictions]);
}

/** Whether a menu path leads somewhere the user cannot open. */
export function restrictedPath(path: string, restrictions: User["restrictions"]): boolean {
  if (!restrictions) return false;
  const access = accessOf(restrictions);
  const [, section, name] = path.split("/");
  if ((section === "app" || section === "masters") && name) return !access.canOpenType(name);
  if (section === "reports" && name) return !access.canOpenReport(name);
  if (section === "accounting" && name?.startsWith("bank-")) return !access.canOpenType("bank-transaction");
  if (section === "rentals") return Object.keys(access.recordScope).length > 0;
  return false;
}

/** A doctype configuration without the fields hidden from the user: the form
 *  then neither shows nor sends them (the server keeps their stored values),
 *  and lists do not offer them as columns. */
export function visibleDoctypeConfig<C extends {
  slug: string;
  fields: { name: string }[];
  childTables: { key: string; fields: { name: string }[] }[];
  listColumns: string[];
  amountField?: string;
}>(config: C | undefined, restrictions: User["restrictions"]): C | undefined {
  const hidden = config ? restrictions?.hidden_fields?.[config.slug] : undefined;
  if (!config || !hidden) return config;
  const header = new Set(hidden[""] ?? []);
  return {
    ...config,
    fields: config.fields.filter((f) => !header.has(f.name)),
    listColumns: config.listColumns.filter((name) => !header.has(name)),
    amountField: config.amountField && header.has(config.amountField) ? undefined : config.amountField,
    childTables: config.childTables.map((table) => {
      const drop = new Set(hidden[table.key] ?? []);
      return drop.size ? { ...table, fields: table.fields.filter((f) => !drop.has(f.name)) } : table;
    }),
  };
}
