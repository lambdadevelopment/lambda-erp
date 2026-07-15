"""Germany — SKR04 localization pack (DATEV Abschlussgliederungsprinzip).

Hand-authored from the published **DATEV SKR04** standard chart — the
balance-sheet-ordered Kontenrahmen (class 0 Anlagevermögen … class 7 further
income/expense), which is DATEV's recommendation for newly founded companies.
Account numbers and their German titles are a German functional standard, not
anyone's copyrightable arrangement, so this is authored from the standard —
cross-checked against the LGPL Odoo ``l10n_de`` template data — rather than
copied from a packaged file, the same stance as the generic and Swiss packs.

A curated ~60-account core proportionate to the Swiss pack (not the full
~1,200-account SKR04); extend as needed. Two German specifics:
  * **EUR** base currency, German account titles carrying their SKR04 number.
  * **Umsatzsteuer / Vorsteuer** — the shared ``make_de_setup_tax`` hook builds
    Sales/Purchase tax templates at the two federal rates (19 % Regelsteuersatz,
    7 % ermäßigt) against the SKR04 USt / Vorsteuer accounts.

Its sibling ``de_skr03`` carries the *same* accounts under the process-ordered
numbering; both register ``country="de"`` variants, so ``resolve_pack("de")``
falls back to the alphabetically-first variant (SKR03) when the caller does not
name one — SKR03 is still the most widely used chart among German SMEs.
"""

from lambda_erp.accounting.setup import spine
from lambda_erp.accounting.setup.pack import LocalizationPack, register_pack
from lambda_erp.accounting.setup.packs.de_common import (
    make_de_setup_tax, STANDARD_RATE, REDUCED_RATE,
)


# ---------------------------------------------------------------------------
# Base chart — SKR04 (curated core). Numbers live in the account name (the
# Account doctype is name-keyed, no separate code column). root_type /
# report_type inherit from the parent; set on the five roots. Group accounts
# (with children) carry no account_type; leaves carry the posting role.
# ---------------------------------------------------------------------------
DE_SKR04_CHART = {
    "0-1 Aktiva": {
        "root_type": "Asset",
        "report_type": "Balance Sheet",
        "children": {
            "0 Anlagevermögen": {
                "children": {
                    "0400 Technische Anlagen und Maschinen": {"account_type": "Fixed Asset"},
                    "0500 Andere Anlagen, Betriebs- und Geschäftsausstattung": {"account_type": "Fixed Asset"},
                    "0520 Pkw": {"account_type": "Fixed Asset"},
                    "0540 Lkw": {"account_type": "Fixed Asset"},
                    "0670 Betriebsausstattung": {"account_type": "Fixed Asset"},
                    "0690 Geschäftsausstattung": {"account_type": "Fixed Asset"},
                    "0810 Abschreibung auf Sachanlagen (Wertberichtigung)": {"account_type": "Accumulated Depreciation"},
                },
            },
            "1 Umlaufvermögen": {
                "children": {
                    "1000 Roh-, Hilfs- und Betriebsstoffe": {"account_type": "Stock"},
                    "1100 Fertige Erzeugnisse und Waren (Bestand)": {"account_type": "Stock"},
                    "1140 Waren (Bestand)": {"account_type": "Stock"},
                    "1200 Forderungen aus Lieferungen und Leistungen": {"account_type": "Receivable"},
                    "1210 Zweifelhafte Forderungen": {"account_type": ""},
                    "1401 Abziehbare Vorsteuer 7 %": {"account_type": ""},
                    "1406 Abziehbare Vorsteuer 19 %": {"account_type": ""},
                    "1460 Geldtransit": {"account_type": ""},
                    "1600 Kasse": {"account_type": "Cash"},
                    "1800 Bank": {"account_type": "Bank"},
                    "1900 Aktive Rechnungsabgrenzung": {"account_type": ""},
                },
            },
        },
    },
    "2 Eigenkapital": {
        "root_type": "Equity",
        "report_type": "Balance Sheet",
        "children": {
            "2000 Festkapital / Gezeichnetes Kapital": {"account_type": ""},
            "2100 Kapitalrücklage": {"account_type": ""},
            "2970 Gewinnvortrag vor Verwendung": {"account_type": ""},
            "2978 Verlustvortrag vor Verwendung": {"account_type": ""},
            "9000 Saldenvorträge, Sachkonten (Eröffnungsbilanz)": {"account_type": ""},
        },
    },
    "3 Fremdkapital": {
        "root_type": "Liability",
        "report_type": "Balance Sheet",
        "children": {
            "30 Kurzfristige Verbindlichkeiten": {
                "children": {
                    "3300 Verbindlichkeiten aus Lieferungen und Leistungen": {"account_type": "Payable"},
                    "3395 Noch nicht abgerechnete Wareneingänge": {"account_type": "Stock Received But Not Billed"},
                    "3740 Verbindlichkeiten aus Lohn und Gehalt": {"account_type": ""},
                    "3790 Verbindlichkeiten im Rahmen der sozialen Sicherheit": {"account_type": ""},
                    "3801 Umsatzsteuer 7 %": {"account_type": "Tax"},
                    "3806 Umsatzsteuer 19 %": {"account_type": "Tax"},
                    "3900 Passive Rechnungsabgrenzung": {"account_type": ""},
                },
            },
            "34 Langfristige Verbindlichkeiten": {
                "children": {
                    "3150 Verbindlichkeiten gegenüber Kreditinstituten": {"account_type": ""},
                    "3170 Verbindlichkeiten aus Darlehen": {"account_type": ""},
                },
            },
        },
    },
    "4 Umsatzerlöse und betriebliche Erträge": {
        "root_type": "Income",
        "report_type": "Profit and Loss",
        "children": {
            "4000 Umsatzerlöse": {"account_type": "Income Account"},
            "4300 Erlöse 7 % USt": {"account_type": "Income Account"},
            "4400 Erlöse 19 % USt": {"account_type": "Income Account"},
            "4700 Erlösschmälerungen (Skonti, Rabatte)": {"account_type": ""},
            "4830 Sonstige betriebliche Erträge": {"account_type": ""},
            "4840 Erträge aus Währungsumrechnung": {"account_type": ""},
        },
    },
    "5-7 Aufwendungen": {
        "root_type": "Expense",
        "report_type": "Profit and Loss",
        "children": {
            "5 Material- und Wareneinkauf": {
                "children": {
                    "5000 Aufwendungen für Roh-, Hilfs- und Betriebsstoffe": {"account_type": "Cost of Goods Sold"},
                    "5200 Wareneingang": {"account_type": "Cost of Goods Sold"},
                    "5300 Wareneingang 7 % Vorsteuer": {"account_type": "Cost of Goods Sold"},
                    "5400 Wareneingang 19 % Vorsteuer": {"account_type": "Cost of Goods Sold"},
                    "5800 Bezugsnebenkosten": {"account_type": "Chargeable"},
                    "5820 Zölle und Einfuhrabgaben": {"account_type": "Chargeable"},
                    "5883 Bestandsveränderungen": {"account_type": "Stock Adjustment"},
                },
            },
            "6 Sonstige betriebliche Aufwendungen": {
                "children": {
                    "6000 Löhne und Gehälter": {"account_type": ""},
                    "6110 Gesetzliche soziale Aufwendungen": {"account_type": ""},
                    "6305 Raumkosten": {"account_type": ""},
                    "6310 Miete (unbewegliche Wirtschaftsgüter)": {"account_type": ""},
                    "6400 Versicherungen, Beiträge und Abgaben": {"account_type": ""},
                    "6500 Fahrzeugkosten": {"account_type": ""},
                    "6600 Werbekosten": {"account_type": ""},
                    "6650 Reisekosten": {"account_type": ""},
                    "6800 Porto, Telefon und Bürobedarf": {"account_type": ""},
                    "6220 Abschreibungen auf Sachanlagen": {"account_type": "Depreciation"},
                    "6880 Kursdifferenzen (Währungsumrechnung)": {"account_type": ""},
                    "6885 Kursdifferenzen (nicht realisiert)": {"account_type": ""},
                    "6969 Rundungsdifferenzen": {"account_type": "Round Off"},
                },
            },
            "7 Weitere Aufwendungen": {
                "children": {
                    "7300 Zinsen und ähnliche Aufwendungen": {"account_type": ""},
                    "7600 Steuern vom Einkommen und Ertrag": {"account_type": ""},
                },
            },
        },
    },
}


DE_SKR04_ANCHORS = {
    spine.CURRENT_ASSETS: "1 Umlaufvermögen",
    spine.FIXED_ASSETS: "0 Anlagevermögen",
    spine.CURRENT_LIABILITIES: "30 Kurzfristige Verbindlichkeiten",
    spine.EQUITY: "2 Eigenkapital",
    spine.INCOME: "4 Umsatzerlöse und betriebliche Erträge",
    spine.DIRECT_COSTS: "5 Material- und Wareneinkauf",
    spine.OPERATING_EXPENSES: "6 Sonstige betriebliche Aufwendungen",
}


# Every default the posting engine references must resolve to an SKR04 leaf, or
# postings that fall back to it will fail. Keys mirror STANDARD_DEFAULTS.
DE_SKR04_DEFAULTS = {
    "default_receivable_account": "1200 Forderungen aus Lieferungen und Leistungen",
    "default_payable_account": "3300 Verbindlichkeiten aus Lieferungen und Leistungen",
    "default_income_account": "4400 Erlöse 19 % USt",
    "default_expense_account": "5400 Wareneingang 19 % Vorsteuer",
    "round_off_account": "6969 Rundungsdifferenzen",
    "stock_in_hand_account": "1140 Waren (Bestand)",
    "stock_received_but_not_billed": "3395 Noch nicht abgerechnete Wareneingänge",
    "stock_adjustment_account": "5883 Bestandsveränderungen",
    "default_opening_balance_equity": "9000 Saldenvorträge, Sachkonten (Eröffnungsbilanz)",
    "default_freight_in_account": "5800 Bezugsnebenkosten",
    "default_customs_account": "5820 Zölle und Einfuhrabgaben",
    "default_exchange_gain_loss_account": "6880 Kursdifferenzen (Währungsumrechnung)",
    "default_unrealized_exchange_account": "6885 Kursdifferenzen (nicht realisiert)",
}


# German VAT wiring — (title, rate, account_leaf). Output tax = Umsatzsteuer
# (liability), input tax = Vorsteuer (recoverable asset).
_SALES_TAXES = [
    ("Umsatzsteuer 19 % (Regelsteuersatz)", STANDARD_RATE, "3806 Umsatzsteuer 19 %"),
    ("Umsatzsteuer 7 % (ermäßigt)", REDUCED_RATE, "3801 Umsatzsteuer 7 %"),
]
_PURCHASE_TAXES = [
    ("Vorsteuer 19 %", STANDARD_RATE, "1406 Abziehbare Vorsteuer 19 %"),
    ("Vorsteuer 7 %", REDUCED_RATE, "1401 Abziehbare Vorsteuer 7 %"),
]

de_skr04_setup_tax = make_de_setup_tax(_SALES_TAXES, _PURCHASE_TAXES)


DE_SKR04_PACK = register_pack(LocalizationPack(
    country="de",
    variant="skr04",
    label="Germany (DATEV SKR04)",
    currency="EUR",
    language="de",
    base_chart=DE_SKR04_CHART,
    anchors=DE_SKR04_ANCHORS,
    defaults=DE_SKR04_DEFAULTS,
    setup_tax=de_skr04_setup_tax,
    notes=(
        "German SME chart, DATEV SKR04 (balance-sheet-ordered), German titles "
        "with SKR04 numbers, EUR. Umsatzsteuer/Vorsteuer templates at 19 / 7 %. "
        "Hand-authored from the published standard, cross-checked against Odoo "
        "l10n_de. DATEV's recommendation for newly founded companies; the "
        "process-ordered SKR03 sibling is the default for a bare `de`."
    ),
))
