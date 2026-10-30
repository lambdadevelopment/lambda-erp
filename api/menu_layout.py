"""Personal sidebar menu: hide and reorder groups and items, per user.

The menu itself is defined by the frontend (core groups plus whatever plugins
register), so the web app publishes what it shows to each user as the
`nav_catalog` preference. Reads and writes here validate against that catalog;
the layout is stored as the `nav_layout` preference. Hiding only tidies the
menu: routes and permissions are unchanged.
"""
import json

from pydantic import Field

from api import mentions, services
from lambda_erp.database import get_db

CATALOG_KEY = "nav_catalog"
LAYOUT_KEY = "nav_layout"
# The page that edits the menu can never be hidden, or a user could lock
# themselves out of undoing it. Its group therefore can't be hidden either.
LOCKED_ITEM = "/settings/menu"
REF = Field(min_length=1, max_length=200)


def _actor(user):
    if (user or {}).get("_shared_connection"):
        raise PermissionError("The menu is personal. The shared team key identifies its owner, "
                              "not the current user; connect with a personal ERP account.")
    from api.auth import refresh_auth_principal
    actor = refresh_auth_principal(user)
    if not actor or actor.get("role") not in mentions.REAL_ROLES:
        raise PermissionError("Sign in with a personal ERP account to change your menu.")
    return actor.get("user_id") or actor["name"]


def _read(user_name, key, default):
    rows = get_db().sql('SELECT value FROM "User Preference" WHERE user_name = ? AND key = ?',
                        [user_name, key])
    try:
        return json.loads(rows[0]["value"]) if rows else default
    except (TypeError, ValueError):
        return default


def _write(user_name, key, value):
    db, text = get_db(), json.dumps(value, separators=(",", ":"))
    with db.atomic():
        if db.sql('SELECT 1 FROM "User Preference" WHERE user_name = ? AND key = ?', [user_name, key]):
            db.sql('UPDATE "User Preference" SET value = ? WHERE user_name = ? AND key = ?',
                   [text, user_name, key])
        else:
            db.sql('INSERT INTO "User Preference" (user_name, key, value) VALUES (?, ?, ?)',
                   [user_name, key, text])


def _layout(user_name):
    raw = _read(user_name, LAYOUT_KEY, {})
    raw = raw if isinstance(raw, dict) else {}
    items = raw.get("items") if isinstance(raw.get("items"), dict) else {}
    return {"order": [k for k in raw.get("order") or [] if isinstance(k, str)],
            "items": {g: [k for k in v if isinstance(k, str)] for g, v in items.items() if isinstance(v, list)},
            "hidden": [k for k in raw.get("hidden") or [] if isinstance(k, str)]}


def _catalog(user_name):
    raw = _read(user_name, CATALOG_KEY, [])
    groups = []
    for group in raw if isinstance(raw, list) else []:
        if not isinstance(group, dict) or not isinstance(group.get("key"), str):
            continue
        items = [{"key": i["key"], "label": str(i.get("label") or i["key"]), "title": str(i.get("title") or "")}
                 for i in group.get("items") or [] if isinstance(i, dict) and isinstance(i.get("key"), str)]
        groups.append({"key": group["key"], "label": str(group.get("label") or group["key"]),
                       "title": str(group.get("title") or ""), "items": items})
    return groups


def _ordered(default, preferred):
    """Keys named in `preferred` first, in that order; the rest keep their default order."""
    first = [k for k in dict.fromkeys(preferred) if k in default]
    return first + [k for k in default if k not in first]


def effective_menu(catalog, layout):
    hidden = set(layout["hidden"])
    by_key = {g["key"]: g for g in catalog}
    menu = []
    for key in _ordered([g["key"] for g in catalog], layout["order"]):
        group = by_key[key]
        items = {i["key"]: i for i in group["items"]}
        menu.append({**{k: group[k] for k in ("key", "label", "title")}, "hidden": key in hidden,
                     "items": [{**items[i], "hidden": i in hidden, "locked": i == LOCKED_ITEM}
                               for i in _ordered(list(items), layout["items"].get(key, []))],
                     "locked": LOCKED_ITEM in items})
    return menu


def _resolver(catalog):
    """Map a reference (group key, item path, or an English or translated label) to a key."""
    exact, named = {}, {}
    for group in catalog:
        exact[group["key"]] = ("group", group["key"], None)
        for item in group["items"]:
            exact[item["key"]] = ("item", item["key"], group["key"])
    for group in catalog:
        refs = [(group, "group", None)] + [(item, "item", group["key"]) for item in group["items"]]
        for entry, kind, parent in refs:
            for name in {entry["label"].casefold(), entry["title"].casefold()} - {""}:
                named.setdefault(name, set()).add((kind, entry["key"], parent))

    def resolve(ref):
        if ref in exact:
            return exact[ref]
        matches = named.get(ref.strip().casefold(), set())
        if len(matches) == 1:
            return next(iter(matches))
        if matches:
            raise ValueError(f"{ref!r} is ambiguous: use one of the keys "
                             f"{', '.join(sorted(m[1] for m in matches))}.")
        raise ValueError(f"Unknown menu entry {ref!r}. Call get_menu_layout for the available "
                         "groups and items.")
    return resolve


class GetLayout(mentions.Input):
    pass


class UpdateLayout(mentions.Input):
    hide: list[str] = Field(default_factory=list, max_length=200)
    show: list[str] = Field(default_factory=list, max_length=200)
    group_order: list[str] | None = Field(default=None, max_length=200)
    item_order: dict[str, list[str]] = Field(default_factory=dict, max_length=100)
    reset: bool = False


def _response(catalog, layout, note=None):
    result = {"menu": effective_menu(catalog, layout), "layout": layout}
    if not catalog:
        result["note"] = ("Your menu is not known yet: open the ERP web app once, then try again.")
    if note:
        result["note"] = note
    return result


def get_menu_layout(args, user):
    GetLayout.model_validate(args)
    user_name = _actor(user)
    return _response(_catalog(user_name), _layout(user_name))


def update_menu_layout(args, user):
    data, user_name = UpdateLayout.model_validate(args), _actor(user)
    catalog = _catalog(user_name)
    if not catalog:
        raise ValueError("Your menu is not known yet: open the ERP web app once, then try again.")
    resolve = _resolver(catalog)
    layout = {"order": [], "items": {}, "hidden": []} if data.reset else _layout(user_name)
    hidden = dict.fromkeys(layout["hidden"])
    for ref in data.show:
        hidden.pop(resolve(ref)[1], None)
    locked_group = next((g["key"] for g in catalog if any(i["key"] == LOCKED_ITEM for i in g["items"])), None)
    for ref in data.hide:
        kind, key, _ = resolve(ref)
        if key == LOCKED_ITEM or key == locked_group:
            raise ValueError(f"{ref!r} can't be hidden: it holds the menu settings page. "
                             "Hide the other items in it instead.")
        hidden[key] = None
    layout["hidden"] = list(hidden)
    if data.group_order is not None:
        keys = [resolve(ref) for ref in data.group_order]
        if any(kind != "group" for kind, _, _ in keys):
            raise ValueError("group_order takes groups only; reorder items with item_order.")
        layout["order"] = [key for _, key, _ in keys]
    for group_ref, refs in data.item_order.items():
        kind, group, _ = resolve(group_ref)
        if kind != "group":
            raise ValueError(f"item_order keys are groups; {group_ref!r} is an item.")
        keys = [resolve(ref) for ref in refs]
        if any(kind != "item" or parent != group for kind, _, parent in keys):
            raise ValueError(f"item_order[{group_ref!r}] may only list items of that group; "
                             "items can't move between groups.")
        layout["items"][group] = [key for _, key, _ in keys]
    _write(user_name, LAYOUT_KEY, layout)
    return _response(catalog, layout)


def initialize_menu_layout():
    services.register_action(
        "get_menu_layout", get_menu_layout, minimum_role="viewer", pass_user=True,
        parameters=GetLayout.model_json_schema(),
        description=(
            "Show the current user's personal sidebar menu: every group and item in display order, "
            "with its key, English label, label in the user's language (title) and whether it is "
            "hidden. Call this before update_menu_layout."))
    services.register_action(
        "update_menu_layout", update_menu_layout, minimum_role="viewer", pass_user=True,
        parameters=UpdateLayout.model_json_schema(),
        description=(
            "Hide, show or reorder entries in the current user's own sidebar menu (no one else's). "
            "Refer to an entry by its key from get_menu_layout (a group label such as 'Rentals', or "
            "an item path such as '/app/asset') or by its label. hide/show take groups or items; "
            "hiding a group hides all of it. group_order lists groups to put first, in that order "
            "(the rest follow); item_order maps a group to its items in the new order. Items stay "
            "in their group. reset=true restores the default menu before applying the rest. The "
            "menu settings entry can't be hidden. Hiding only tidies the menu: pages stay "
            "reachable and permissions are unchanged."))
