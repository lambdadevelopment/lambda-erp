// Personal menu layout: drag sidebar groups and items into order and hide the
// ones you don't use, then save (api/menu_layout.py). The same changes are
// available to the chat, the REST API (update_menu_layout) and MCP clients.
import { useEffect, useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Eye, EyeOff, GripVertical, Lock } from "lucide-react";
import { api } from "@/api/client";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { SaveButton } from "@/components/ui/save-button";
import { Sortable, type SortableHandleProps } from "@/components/ui/sortable";
import { useMySettings } from "@/hooks/use-my-settings";
import { useUnsavedChanges } from "@/lib/use-unsaved-changes";
import { MENU_SETTINGS_PATH, isLocked, ordered, parseLayout, type CatalogGroup, type NavLayout } from "@/lib/nav-layout";
import { cn } from "@/lib/utils";

function parseCatalog(raw: string | undefined): CatalogGroup[] {
  try {
    const value = raw ? JSON.parse(raw) : [];
    return Array.isArray(value) ? value : [];
  } catch {
    return [];
  }
}

/** The whole menu in display order, as edited on this page before saving. */
type Draft = { groups: CatalogGroup[]; hidden: Set<string> };

function draftFrom(catalog: CatalogGroup[], layout: NavLayout): Draft {
  return {
    groups: ordered(catalog, (g) => g.key, layout.order)
      .map((g) => ({ ...g, items: ordered(g.items, (i) => i.key, layout.items[g.key]) })),
    hidden: new Set(layout.hidden.filter((key) => catalog.some((g) => g.key === key || g.items.some((i) => i.key === key)))),
  };
}

const sameOrder = (a: { key: string }[], b: { key: string }[]) =>
  a.length === b.length && a.every((x, i) => x.key === b[i].key);

function Handle({ label, handle }: { label: string; handle: SortableHandleProps }) {
  const { t } = useTranslation();
  return (
    <button type="button" {...handle} aria-label={t("menuLayout.drag", { name: label })}
      title={t("menuLayout.drag", { name: label })}
      className="-ml-1 rounded p-1 text-fg-muted transition-colors hover:bg-surface-subtle hover:text-fg focus:outline-none focus-visible:ring-2 focus-visible:ring-brand/40">
      <GripVertical className="h-4 w-4" />
    </button>
  );
}

function Visibility({ label, hidden, locked, onToggle }: {
  label: string; hidden: boolean; locked: boolean; onToggle: () => void;
}) {
  const { t } = useTranslation();
  if (locked) {
    return <span className="p-1.5 text-fg-muted" title={t("menuLayout.locked")}><Lock className="h-4 w-4" /></span>;
  }
  const text = t(hidden ? "menuLayout.show" : "menuLayout.hide", { name: label });
  return (
    <button type="button" onClick={onToggle} aria-pressed={!hidden} aria-label={text} title={text}
      className="rounded p-1.5 text-fg-muted transition-colors hover:bg-surface-subtle hover:text-fg">
      {hidden ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
    </button>
  );
}

export default function MenuLayoutPage() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const { settings, isLoaded } = useMySettings();
  const catalog = useMemo(() => parseCatalog(settings.nav_catalog), [settings.nav_catalog]);
  const saved = useMemo(() => draftFrom(catalog, parseLayout(settings.nav_layout)), [catalog, settings.nav_layout]);
  const defaults = useMemo(() => draftFrom(catalog, parseLayout(undefined)), [catalog]);
  const [draft, setDraft] = useState<Draft>(saved);
  const { dirty, markDirty, markClean } = useUnsavedChanges();

  // Follow the saved menu (first load, the chat changing it) unless the user
  // is in the middle of editing.
  useEffect(() => { if (!dirty) setDraft(saved); }, [saved, dirty]);

  const edit = (next: Draft) => { setDraft(next); markDirty(); };
  const toggle = (key: string) => {
    const hidden = new Set(draft.hidden);
    if (!hidden.delete(key)) hidden.add(key);
    edit({ ...draft, hidden });
  };
  const reorderItems = (groupKey: string, items: CatalogGroup["items"]) =>
    edit({ ...draft, groups: draft.groups.map((g) => (g.key === groupKey ? { ...g, items } : g)) });

  // Save the draft as a fresh layout that records only what differs from the
  // default, so menu entries added later still appear in their default place.
  const save = useMutation({
    mutationFn: () => {
      const item_order: Record<string, string[]> = {};
      for (const group of draft.groups) {
        const base = defaults.groups.find((g) => g.key === group.key);
        if (base && !sameOrder(group.items, base.items)) item_order[group.key] = group.items.map((i) => i.key);
      }
      return api.runAction("update_menu_layout", {
        reset: true,
        hide: [...draft.hidden],
        ...(sameOrder(draft.groups, defaults.groups) ? {} : { group_order: draft.groups.map((g) => g.key) }),
        item_order,
      }) as Promise<{ layout: NavLayout }>;
    },
    onSuccess: ({ layout }) => {
      markClean();
      qc.setQueryData<Record<string, string>>(["my-settings"],
        (prev) => ({ ...(prev ?? {}), nav_layout: JSON.stringify(layout) }));
    },
  });

  const isDefault = sameOrder(draft.groups, defaults.groups) && draft.hidden.size === 0
    && draft.groups.every((g, i) => sameOrder(g.items, defaults.groups[i].items));

  if (!isLoaded) return <p className="text-fg-muted">{t("common.loading")}</p>;

  return (
    <div className="space-y-6">
      <Card>
        <h3 className="text-base font-semibold tracking-tight text-fg">{t("menuLayout.title")}</h3>
        <p className="mt-1 max-w-2xl text-sm text-fg-muted">{t("menuLayout.intro")}</p>
        <p className="mt-2 max-w-2xl text-xs text-fg-muted">{t("menuLayout.chatHint")}</p>
      </Card>

      {/* Actions stay in reach while dragging down a long menu. */}
      <div className="sticky top-0 z-20 flex flex-wrap items-center justify-end gap-2 rounded-xl bg-surface px-4 py-2 ring-1 ring-line shadow-card">
        {save.error ? (
          <p role="alert" className="mr-auto text-sm text-rose-700">{save.error.message}</p>
        ) : dirty && <p className="mr-auto text-sm text-fg-muted">{t("menuLayout.unsaved")}</p>}
        <Button variant="ghost" size="sm" disabled={isDefault || save.isPending} onClick={() => edit(defaults)}>
          {t("menuLayout.reset")}
        </Button>
        {dirty && (
          <Button variant="secondary" size="sm" disabled={save.isPending}
            onClick={() => { setDraft(saved); markClean(); save.reset(); }}>
            {t("menuLayout.discard")}
          </Button>
        )}
        <SaveButton dirty={dirty} saving={save.isPending} onClick={() => save.mutate()} />
      </div>

      <Sortable items={draft.groups} keyOf={(g) => g.key} className="space-y-3"
        onReorder={(groups) => edit({ ...draft, groups })}>
        {(group, { handle, dragging }) => {
          const title = group.title || group.label;
          const groupHidden = draft.hidden.has(group.key);
          return (
            <div className={cn("rounded-xl bg-surface ring-1 ring-line shadow-card transition-shadow",
              dragging && "relative z-10 shadow-card-hover ring-2 ring-brand/40")}>
              <div className="flex items-center gap-2 px-4 py-3">
                <Handle label={title} handle={handle} />
                <span className={cn("min-w-0 flex-1 truncate text-sm font-semibold uppercase tracking-wider",
                  groupHidden ? "text-fg-muted line-through" : "text-fg")}>
                  {title}
                </span>
                <Visibility label={title} hidden={groupHidden} locked={isLocked(group.items)}
                  onToggle={() => toggle(group.key)} />
              </div>
              {group.items.length > 0 && (
                <Sortable items={group.items} keyOf={(i) => i.key}
                  className={cn("border-t border-line py-1", groupHidden && "opacity-50")}
                  onReorder={(items) => reorderItems(group.key, items)}>
                  {(item, { handle: itemHandle, dragging: itemDragging }) => {
                    const itemTitle = item.title || item.label;
                    const itemHidden = draft.hidden.has(item.key);
                    return (
                      <div className={cn("flex items-center gap-2 py-1 pl-8 pr-4",
                        itemDragging && "relative z-10 rounded-lg bg-surface shadow-card-hover ring-2 ring-brand/40")}>
                        <Handle label={itemTitle} handle={itemHandle} />
                        <span className={cn("min-w-0 flex-1 truncate text-sm",
                          itemHidden ? "text-fg-muted line-through" : "text-fg")}>
                          {itemTitle}
                        </span>
                        <Visibility label={itemTitle} hidden={itemHidden}
                          locked={item.key === MENU_SETTINGS_PATH} onToggle={() => toggle(item.key)} />
                      </div>
                    );
                  }}
                </Sortable>
              )}
            </div>
          );
        }}
      </Sortable>
    </div>
  );
}
