"""Lightweight lifecycle hook registry for the core+extension model.

A customer deployment (a separate repo depending on this core) registers
handlers for document lifecycle events without editing core files. Events are
named ``"<DocType>:<phase>"`` where phase is one of:

    before_save / after_save
    before_submit / on_submit / after_submit
    before_cancel / on_cancel / after_cancel

Semantics (see docs/core-extension-architecture.md):
- ``before_*`` fire inside the document's transaction — a raising handler
  aborts and rolls back the operation (use for extra validation / guards).
- ``on_submit`` / ``on_cancel`` fire inside the transaction too, after the
  document has posted (or reversed) its ledgers and order progress has been
  recalculated — the place for a follow-up step that must succeed or fail
  together with the document. A raising handler rolls everything back.
- ``after_*`` fire after the change is committed and durable — use for
  side-effects (notifications, external sync). A raising ``after_*`` handler
  propagates but does NOT undo the committed voucher.
"""

from collections import defaultdict

_HOOKS: dict[str, list] = defaultdict(list)


def register_hook(event: str, fn) -> None:
    """Register a callable ``fn(doc, *args, **kwargs)`` for an event."""
    _HOOKS[event].append(fn)


def run_hooks(event: str, *args, **kwargs) -> None:
    """Invoke every handler registered for ``event``, in registration order."""
    for fn in _HOOKS.get(event, ()):  # plain .get avoids creating empty lists
        fn(*args, **kwargs)


def clear_hooks() -> None:
    """Remove all registered handlers (used for test isolation)."""
    _HOOKS.clear()
