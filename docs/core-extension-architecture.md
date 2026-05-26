# Core + Extension architecture — implementation plan

Status: **Phases 1–4 implemented** (2026-05-26): the document-class registry
(`api/services.register_doctype`), the lifecycle hooks layer
(`lambda_erp/hooks.py`, wired into `Document.save/submit/cancel`), plugin
startup wiring (`api/main.load_plugins` via `LAMBDA_ERP_PLUGINS`), and the
converters seam (`register_converter` + `convert_document` upgrades the
produced instance to a registered subclass in place). The README "Building a
customer deployment on top of the core" section is written. Covered by
`tests/test_erp_validation.py` sections 42 (registry/hooks/converter) and 43
(plugin loader).

**Frontend override seam — done (2026-05-26).** Implemented as part of
packaging Phase B (see `packaging-distribution-plan.md`): doctype/route/nav/
component registries, runtime branding/theme (`configureBranding`),
configurable API base, a shareable Tailwind preset, and a dual app/library
build shipping `@lambda-development/erp-core`. The README "Building a customer deployment"
section documents the frontend usage.

Phase 4 note: rather than migrating each `make_*` helper, `convert_document`
reassigns the produced base instance's `__class__` to the registered override
subclass (safe — no `__slots__`, override is a subclass). `register_converter`
remains available for replacing conversion *logic*, not just the class.

## Goal

Let a customer deployment live in its **own private repo that depends on this
repo as the core**, and **override or extend core business logic** without
forking core files. A core bug/feature fix then reaches every customer via a
version bump, not a manual merge into N diverging forks.

Two override mechanisms:

- **Replace** logic → subclass a core document class and register it (the
  document loader resolves classes from a registry).
- **Add** behavior (side-effects, extra validation, integrations) → register a
  **hook** that fires at a document lifecycle point.

Branding / enabled features / base currency / OAuth config stay **config/env**,
not code (already partly true: `LAMBDA_ERP_DEMO_CURRENCY`, demo flags, i18n).

Frontend customization (component/route registry + Vite alias + theming) is a
**separate seam** and out of scope for this plan.

---

## Phase 1 — Document-class registry seam

`api/services.py` already holds `DOCUMENT_CLASSES`, `CONVERTERS`,
`SLUG_TO_DOCTYPE`, `DOCTYPE_TO_SLUG`, and `get_document_class()` reads
`DOCUMENT_CLASSES[doctype]` **at call time**. All of
`create/load/update/submit/cancel_document` route through `get_document_class`,
so making the registry mutable makes every one of those paths honor an override.

Tasks:
- [ ] Add to `api/services.py`:
  ```python
  def register_doctype(doctype: str, cls, slug: str | None = None):
      DOCUMENT_CLASSES[doctype] = cls
      slug = slug or doctype.lower().replace(" ", "-")
      SLUG_TO_DOCTYPE[slug] = doctype
      DOCTYPE_TO_SLUG[doctype] = slug
  ```
- [ ] Leave `get_document_class` unchanged (already resolves live).
- [ ] Audit for spots that import a concrete document class **directly** instead
  of going through `services` (chat tool handlers in `api/chat.py`, `api/pdf.py`,
  routers). Each such spot bypasses the override. Where reasonable, route through
  `get_document_class`; otherwise note it as a known non-seam.

Acceptance: registering `AcmeSalesInvoice` for `"Sales Invoice"` causes
`create/load/submit_document("sales-invoice", …)` to use the subclass.

## Phase 2 — Hooks layer

New module `lambda_erp/hooks.py`:
```python
from collections import defaultdict
_HOOKS = defaultdict(list)
def register_hook(event: str, fn): _HOOKS[event].append(fn)
def run_hooks(event: str, *args, **kwargs):
    for fn in _HOOKS.get(event, ()):
        fn(*args, **kwargs)
def clear_hooks(): _HOOKS.clear()   # test isolation
```

Anchor `run_hooks` in the **base `Document`** (`lambda_erp/model.py`) so every
doctype gets uniform points (current anchors: `save()`@271, `validate()` call
@288; `submit()`@294, `on_submit()` call @319; `cancel()`@349, `on_cancel()`
@349):
- [ ] `before_save` / `after_save` around the persist in `save()`.
- [ ] `before_submit` / `after_submit` around `self.on_submit()` in `submit()`.
- [ ] `before_cancel` / `after_cancel` around `self.on_cancel()` in `cancel()`.
- [ ] Event name convention: `f"{self.DOCTYPE}:after_submit"`.

Implemented semantics: **`before_*` fire inside** the transaction — a raising
handler aborts and rolls back the operation (guards / extra validation,
consistent with the cancel-guard invariant in `docs/agents/invariants.md`).
**`after_*` fire post-commit** — the voucher is durable, so they're for
side-effects (notifications, external sync) and a raise there does NOT undo the
committed voucher.

Acceptance: a registered `"Sales Invoice:after_submit"` handler fires once per
submit and a raising `before_submit` aborts the submit.

## Phase 3 — Plugin startup wiring

- [ ] In `api/main.py` lifespan, **after `setup()`**, import customer plugin
  modules and call their `register()`:
  ```python
  import importlib, os
  for mod in filter(None, (m.strip() for m in os.environ.get("LAMBDA_ERP_PLUGINS", "").split(","))):
      importlib.import_module(mod).register()
  ```
- [ ] Core repo runs with `LAMBDA_ERP_PLUGINS` unset → behavior identical to today
  (additive, zero-risk default).
- [ ] (Later) optionally replace env discovery with setuptools entry points.

## Phase 4 — Converters seam (the second seam)

`convert_document` uses `CONVERTERS[(source, target)]`, and the `make_*` helpers
(`make_sales_order`, `make_sales_invoice`, `make_purchase_invoice`,
`make_delivery_note`, returns, …) construct **concrete base classes inside the
core modules**. So an overridden class is **not** produced by a conversion. Fix:

- [ ] Add `register_converter(source, target, fn)` to `api/services.py`
  (mutator over the existing `CONVERTERS` dict).
- [ ] Make the produced document resolve its class via the registry. Cleanest:
  the `make_*` helpers build a plain dict of field values and hand it to
  `get_document_class(target_slug)` to instantiate, instead of `SalesOrder(...)`
  directly. Migrate helpers **incrementally** — only when a customer overrides a
  doctype that is produced by conversion.
- [ ] Until migrated, document that conversions yield the **base** class even if
  the doctype is overridden (a customer can `register_converter` to work around
  it per case).

Acceptance: with `AcmeSalesOrder` registered, `convert_document(quotation →
sales-order)` yields an `AcmeSalesOrder`.

## Testing

Add to `tests/test_erp_validation.py` (or a new `tests/test_extension.py`):
- [ ] Register a throwaway `SalesInvoice` subclass; assert `create/load/submit`
  use it; assert base behavior when nothing is registered.
- [ ] Register a hook; assert it fires on submit; assert a raising `before_submit`
  rolls back (docstatus unchanged, no GL posted).
- [ ] Phase 4: assert a registered subclass is produced by `convert_document`.
- [ ] `clear_hooks()` / restore the registry in teardown so tests don't leak
  overrides into later sections.

## README documentation task

- [ ] Add a top-level section to `README.md` titled **"Building a customer
  deployment on top of the core"** so a future engineer **or LLM** can do the
  exact thing — stand up a new private repo that depends on this core and
  overrides logic. Draft content below; keep it in sync as the seams grow.

````markdown
## Building a customer deployment on top of the core

This repo is the **core product**. A customer deployment is a **separate
private repo that depends on this one** and overrides/extends it — it does NOT
fork or edit core files. Core fixes arrive via a version bump.

### Layout of a customer repo
```
acme-erp/
  pyproject.toml          # depends on lambda-erp-core==<version>
  acme/
    __init__.py
    plugin.py             # register() — wires overrides + hooks
    sales_invoice.py      # e.g. class AcmeSalesInvoice(SalesInvoice): ...
  config/                 # branding, enabled features, base currency, OAuth
  frontend/               # depends on core frontend; overrides via Vite alias
  deploy/                 # Dockerfile, Azure config, env/secrets
```

### Override core business logic (replace)
Subclass the core document class and register it:
```python
# acme/sales_invoice.py
from lambda_erp.accounting.sales_invoice import SalesInvoice
class AcmeSalesInvoice(SalesInvoice):
    def _get_gl_entries(self):
        gl = super()._get_gl_entries()
        # customer-specific posting
        return gl
```

### Add behavior (don't replace) — hooks
```python
from lambda_erp.hooks import register_hook
register_hook("Sales Invoice:after_submit", push_to_external_system)
```
Hook events: `<DocType>:{before,after}_{save,submit,cancel}`.

### Register everything at startup
```python
# acme/plugin.py
from api.services import register_doctype, register_converter
from lambda_erp.hooks import register_hook
from .sales_invoice import AcmeSalesInvoice

def register():
    register_doctype("Sales Invoice", AcmeSalesInvoice)
    register_hook("Sales Invoice:after_submit", push_to_external_system)
```
Point the deployment at it: `LAMBDA_ERP_PLUGINS=acme` (comma-separated for
multiple). The core imports each module and calls `register()` on startup.

### Rules
- Don't fork or edit core files — override at a seam. If the thing you need to
  change isn't a seam yet, **add the seam to the core** (a PR upstream), then
  override from the customer repo.
- Keep branding / feature toggles / currency / auth config in `config`/env.
- Conversions currently yield the **base** class unless you `register_converter`
  (see `docs/core-extension-architecture.md`, Phase 4).
- Bump the core dependency version to pull fixes; never copy core code in.
````

---

## Boundaries / non-goals
- Frontend deep-override (component/route registries + Vite alias + theming) is
  now implemented — see the "Frontend override seam" note above and
  `packaging-distribution-plan.md` Phase B.
- Packaging the core as a distributable (pip wheel + `@lambda-development/erp-core` npm
  library) is done (Phases A & B). Publishing to PyPI/npm (Phase C) and a
  customer-repo template (Phase D) are not done; until then a customer repo
  consumes both via git/path dependencies.

## Relevant code
- `api/services.py` — `DOCUMENT_CLASSES`, `CONVERTERS`, `get_document_class`,
  `create/load/update/submit/cancel/convert_document`.
- `lambda_erp/model.py` — base `Document` (`save`/`submit`/`cancel` lifecycle).
- `api/main.py` — lifespan startup (plugin wiring).
- `api/chat.py`, `api/pdf.py` — audit for direct concrete-class imports.
