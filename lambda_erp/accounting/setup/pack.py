"""Localization packs — the pluggable jurisdiction layer.

A pack bundles everything that is jurisdiction-specific: the base chart of
accounts, the mapping from semantic *anchors* to that chart's real group
accounts, the company default-account wiring, and a **tax hook** (the "hybrid"
half — Python, because reverse-charge / fiscal-position logic can't be expressed
as flat data). Everything else — the setup flow, the sector overlays — is
jurisdiction-agnostic and lives one layer up in the engine.

Packs are keyed by ``country[.variant]``:

    "generic"      the jurisdiction-neutral / international pack (and fallback)
    "ch"           Switzerland
    "de.skr03"     Germany, SKR03 variant   ┐ same country, sibling variants —
    "de.skr04"     Germany, SKR04 variant   ┘ that's why the key carries a variant

``resolve_pack()`` narrows most-specific-first: an exact ``de.skr03`` match wins,
else the country's default variant (``de``), else the generic fallback. So an
unlocalized country transparently gets the generic pack — never an error.
"""

from dataclasses import dataclass, field
from typing import Callable, Optional

GENERIC = "generic"

# key -> LocalizationPack
_REGISTRY: dict[str, "LocalizationPack"] = {}


@dataclass(frozen=True)
class LocalizationPack:
    """One jurisdiction's accounting starting point.

    Attributes:
        country:  ISO-ish country code, or ``"generic"`` for the neutral pack.
        variant:  optional sub-chart discriminator (e.g. "skr03"). ``None`` =
                  the country's default/only chart.
        label:    human name shown in the setup UI / chat.
        currency: the pack's natural default currency (the caller may override).
        base_chart: the account tree, ``{name: {root_type?, report_type?,
                    account_type?, children?}}`` — same shape as the legacy
                    STANDARD_CHART so one writer serves both.
        anchors:  ``{ANCHOR_KEY: account_name_in_base_chart}`` — resolves each
                  semantic anchor to a real group account in *this* pack's tree.
        defaults: ``{company_field: leaf_account_name}`` — the base company
                  default-account wiring (receivable, payable, income, …).
        setup_tax: optional hook ``fn(company_name, currency) -> list[str]`` run
                  after the chart is written; creates tax accounts / templates
                  and returns a short human summary of what it made. ``None`` for
                  jurisdictions with no standard indirect tax (e.g. generic/US).
    """

    country: str
    label: str
    base_chart: dict
    anchors: dict
    defaults: dict
    variant: Optional[str] = None
    currency: str = "USD"
    # Primary language of the chart's account names (ISO 639-1). Sector-profile
    # overlay accounts are rendered in this language so they match the base
    # chart — "en" for the generic pack, "de" for the Swiss KMU pack, etc.
    language: str = "en"
    setup_tax: Optional[Callable[[str, str], list]] = None
    notes: str = ""

    @property
    def key(self) -> str:
        return f"{self.country}.{self.variant}" if self.variant else self.country

    def anchor_account(self, anchor: str) -> Optional[str]:
        """The real group-account name this pack maps ``anchor`` to (or None)."""
        return self.anchors.get(anchor)


def register_pack(pack: "LocalizationPack") -> "LocalizationPack":
    """Add (or replace) a pack in the registry. Returns it for convenience."""
    _REGISTRY[pack.key] = pack
    return pack


def get_pack(key: str) -> Optional["LocalizationPack"]:
    """Exact lookup by full ``country[.variant]`` key."""
    return _REGISTRY.get(key)


def list_packs() -> list["LocalizationPack"]:
    """All registered packs, generic first then alphabetical by key."""
    packs = list(_REGISTRY.values())
    packs.sort(key=lambda p: (p.country != GENERIC, p.key))
    return packs


def _default_variant_for(country: str) -> Optional["LocalizationPack"]:
    """A country's default pack when no variant was requested.

    Prefer a variant-less pack (``country``); otherwise the first registered
    variant for that country (deterministic by key). Returns None if the country
    is entirely unlocalized.
    """
    exact = _REGISTRY.get(country)
    if exact is not None:
        return exact
    variants = [p for p in _REGISTRY.values() if p.country == country]
    if not variants:
        return None
    variants.sort(key=lambda p: p.key)
    return variants[0]


def resolve_pack(country: Optional[str] = None,
                 variant: Optional[str] = None) -> "LocalizationPack":
    """Most-specific-first resolution with a guaranteed generic fallback.

    1. exact ``country.variant`` if given and registered,
    2. the country's default variant,
    3. the generic pack.

    Country codes are matched case-insensitively; ``None``/unknown → generic.
    """
    if country:
        country = country.strip().lower()
    if variant:
        variant = variant.strip().lower()

    if country and country != GENERIC:
        if variant:
            exact = _REGISTRY.get(f"{country}.{variant}")
            if exact is not None:
                return exact
        by_country = _default_variant_for(country)
        if by_country is not None:
            return by_country

    generic = _REGISTRY.get(GENERIC)
    if generic is None:  # pragma: no cover - packs subpackage always registers it
        raise RuntimeError("generic localization pack is not registered")
    return generic
