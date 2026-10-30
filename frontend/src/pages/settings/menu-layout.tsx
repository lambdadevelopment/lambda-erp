// Personal menu layout: hide and reorder sidebar groups and items
// (api/menu_layout.py). The same changes are available to the chat, the REST
// API (update_menu_layout) and MCP clients.
import { useMemo } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { ArrowDown, ArrowUp, Eye, EyeOff, Lock } from "lucide-react";
import { api } from "@/api/client";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { useMySettings } from "@/hooks/use-my-settings";
import {
  MENU_SETTINGS_PATH, isLocked, ordered, parseLayout,
  type CatalogGroup, type NavLayout,
} from "@/lib/nav-layout";
import { cn } from "@/lib/utils";

interface UpdateArgs {
  hide?: string[]; show?: string[]; group_order?: string[];
  item_order?: Record<string, string[]>; reset?: boolean;
}

function parseCatalog(raw: string | undefined): CatalogGroup[] {
  try {
    const value = raw ? JSON.parse(raw) : [];
    return Array.isArray(value) ? value : [];
  } catch {
    return [];
  }
}

function swap<T>(list: T[], index: number, offset: number): T[] {
  const next = [...list];
  [next[index], next[index + offset]] = [next[index + offset], next[index]];
  return next;
}

function Controls({ label, hidden, locked, first, last, disabled, onToggle, onMove }: {
  label: string; hidden: boolean; locked: boolean; first: boolean; last: boolean; disabled: boolean;
  onToggle: () => void; onMove: (offset: number) => void;
}) {
  const { t } = useTranslation();
  const iconButton = "rounded p-1.5 text-fg-muted transition-colors hover:bg-surface-subtle hover:text-fg disabled:opacity-30 disabled:hover:bg-transparent";
  return (
    <span className="flex shrink-0 items-center gap-0.5">
      <button type="button" className={iconButton} disabled={disabled || first} onClick={() => onMove(-1)}
        aria-label={t("menuLayout.moveUp", { name: label })} title={t("menuLayout.moveUp", { name: label })}>
        <ArrowUp className="h-4 w-4" />
      </button>
      <button type="button" className={iconButton} disabled={disabled || last} onClick={() => onMove(1)}
        aria-label={t("menuLayout.moveDown", { name: label })} title={t("menuLayout.moveDown", { name: label })}>
        <ArrowDown className="h-4 w-4" />
      </button>
      {locked ? (
        <span className="p-1.5 text-fg-muted" title={t("menuLayout.locked")}><Lock className="h-4 w-4" /></span>
      ) : (
        <button type="button" className={iconButton} disabled={disabled} onClick={onToggle} aria-pressed={!hidden}
          aria-label={t(hidden ? "menuLayout.show" : "menuLayout.hide", { name: label })}
          title={t(hidden ? "menuLayout.show" : "menuLayout.hide", { name: label })}>
          {hidden ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
        </button>
      )}
    </span>
  );
}

export default function MenuLayoutPage() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const { settings, isLoaded } = useMySettings();
  const catalog = useMemo(() => parseCatalog(settings.nav_catalog), [settings.nav_catalog]);
  const layout = useMemo(() => parseLayout(settings.nav_layout), [settings.nav_layout]);
  const hidden = new Set(layout.hidden);

  const groups = ordered(catalog, (g) => g.key, layout.order).map((group) => ({
    ...group, items: ordered(group.items, (i) => i.key, layout.items[group.key]),
  }));

  const update = useMutation({
    mutationFn: (args: UpdateArgs) => api.runAction("update_menu_layout", args) as Promise<{ layout: NavLayout }>,
    onSuccess: ({ layout: fresh }) => qc.setQueryData<Record<string, string>>(["my-settings"],
      (prev) => ({ ...(prev ?? {}), nav_layout: JSON.stringify(fresh) })),
  });
  const busy = update.isPending;
  const toggle = (key: string) => update.mutate(hidden.has(key) ? { show: [key] } : { hide: [key] });
  const customised = layout.order.length > 0 || layout.hidden.length > 0 || Object.keys(layout.items).length > 0;

  if (!isLoaded) return <p className="text-fg-muted">{t("common.loading")}</p>;

  return (
    <div className="space-y-6">
      <Card>
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0 max-w-xl">
            <h3 className="text-base font-semibold tracking-tight text-fg">{t("menuLayout.title")}</h3>
            <p className="mt-1 text-sm text-fg-muted">{t("menuLayout.intro")}</p>
            <p className="mt-2 text-xs text-fg-muted">{t("menuLayout.chatHint")}</p>
          </div>
          <Button variant="secondary" size="sm" disabled={busy || !customised}
            onClick={() => update.mutate({ reset: true })}>
            {t("menuLayout.reset")}
          </Button>
        </div>
        {update.error && (
          <p role="alert" className="mt-4 rounded-lg bg-rose-50 p-3 text-sm text-rose-700 ring-1 ring-rose-200">
            {update.error.message}
          </p>
        )}
      </Card>

      <ol className="space-y-3">
        {groups.map((group, gi) => {
          const groupHidden = hidden.has(group.key);
          const title = group.title || group.label;
          return (
            <li key={group.key} className="rounded-xl bg-surface ring-1 ring-line shadow-card">
              <div className="flex items-center gap-3 px-4 py-3">
                <span className={cn("min-w-0 flex-1 truncate text-sm font-semibold uppercase tracking-wider",
                  groupHidden ? "text-fg-muted line-through" : "text-fg")}>
                  {title}
                </span>
                <Controls label={title} hidden={groupHidden} locked={isLocked(group.items)}
                  first={gi === 0} last={gi === groups.length - 1} disabled={busy}
                  onToggle={() => toggle(group.key)}
                  onMove={(offset) => update.mutate({ group_order: swap(groups.map((g) => g.key), gi, offset) })} />
              </div>
              {group.items.length > 0 && (
                <ul className={cn("border-t border-line py-1", groupHidden && "opacity-50")}>
                  {group.items.map((item, ii) => {
                    const itemTitle = item.title || item.label;
                    return (
                      <li key={item.key} className="flex items-center gap-3 py-1 pl-8 pr-4">
                        <span className={cn("min-w-0 flex-1 truncate text-sm",
                          hidden.has(item.key) ? "text-fg-muted line-through" : "text-fg")}>
                          {itemTitle}
                        </span>
                        <Controls label={itemTitle} hidden={hidden.has(item.key)}
                          locked={item.key === MENU_SETTINGS_PATH}
                          first={ii === 0} last={ii === group.items.length - 1} disabled={busy || groupHidden}
                          onToggle={() => toggle(item.key)}
                          onMove={(offset) => update.mutate({
                            item_order: { [group.key]: swap(group.items.map((i) => i.key), ii, offset) },
                          })} />
                      </li>
                    );
                  })}
                </ul>
              )}
            </li>
          );
        })}
      </ol>
    </div>
  );
}
