"""Company-setup engine: jurisdiction packs + sector profiles.

This subpackage is the forward-thinking replacement for the single hardcoded
``STANDARD_CHART`` in ``chart_of_accounts.py``. It is built around three layers,
so that shipping one jurisdiction today does not force a rewrite to add the next:

    account_type spine   the universal Frappe taxonomy (root_type + account_type)
       │                 — never per-country; drives every report and posting rule.
       └─ LocalizationPack  a registry keyed by ``country[.variant]`` (e.g. "generic",
            │               "ch", "de.skr03"). Each pack supplies the base chart, its
            │               semantic anchors, company defaults, and a tax hook.
            └─ SectorProfile  a jurisdiction-INDEPENDENT lens (~7 operating modes).
                              It attaches accounts to pack *anchors* and never names a
                              literal account code — so one profile works on every pack.

Adding a country later = dropping a new pack module into ``packs/`` and registering
it. The engine (``engine.py``) never names a specific jurisdiction; "generic" is
simply the first pack in the registry and the permanent fallback for any country we
have not localized.

See ``docs/agents/company_setup.md`` for the rationale and the pack-authoring guide.
"""

from lambda_erp.accounting.setup.spine import (
    ANCHORS,
    ROOT_TYPES,
    ACCOUNT_TYPES,
)
from lambda_erp.accounting.setup.pack import (
    LocalizationPack,
    register_pack,
    get_pack,
    resolve_pack,
    list_packs,
    GENERIC,
)
from lambda_erp.accounting.setup.profiles import (
    SectorProfile,
    get_profile,
    list_profiles,
)
from lambda_erp.accounting.setup.engine import (
    plan_company_setup,
    apply_company_setup,
)

# Importing the packs subpackage registers every built-in pack (generic today,
# ch/de/… later) as a side effect. Keep this import last so the registry is
# populated by the time anything calls resolve_pack().
from lambda_erp.accounting.setup import packs  # noqa: E402,F401

__all__ = [
    "ANCHORS",
    "ROOT_TYPES",
    "ACCOUNT_TYPES",
    "LocalizationPack",
    "register_pack",
    "get_pack",
    "resolve_pack",
    "list_packs",
    "GENERIC",
    "SectorProfile",
    "get_profile",
    "list_profiles",
    "plan_company_setup",
    "apply_company_setup",
]
