"""Germany — SKR03 localization pack (DATEV Prozessgliederungsprinzip).

Hand-authored from the published **DATEV SKR03** standard chart — the
process-ordered Kontenrahmen (class 0 fixed assets & capital, class 1 finance,
class 3 goods inward, class 4 operating expense, class 8 revenue). SKR03 remains
the most widely used chart among German SMEs, so it is the default a bare
``resolve_pack("de")`` lands on; its sibling ``de_skr04`` carries the *same*
accounts under the newer balance-sheet-ordered numbering.

Account numbers and their German titles are a German functional standard, not
anyone's copyrightable arrangement, so this is authored from the standard —
cross-checked against the LGPL Odoo ``l10n_de`` template data — rather than
copied from a packaged file, the same stance as the generic and Swiss packs.

A curated ~60-account core proportionate to the Swiss pack (not the full
~1,270-account SKR03). Two German specifics:
  * **EUR** base currency, German account titles carrying their SKR03 number.
  * **Umsatzsteuer / Vorsteuer** — the shared ``make_de_setup_tax`` hook builds
    Sales/Purchase tax templates at the two federal rates (19 % Regelsteuersatz,
    7 % ermäßigt) against the SKR03 USt / Vorsteuer accounts.

Note the process ordering: several accounts sit in a DATEV class whose leading
digit does not match their balance-sheet section (e.g. ``3980 Waren (Bestand)``
is an *asset* though it is numbered in the class-3 goods band, and ``1600
Verbindlichkeiten aLuL`` is a *liability* though numbered in the class-1 finance
band). The tree is organised by ``root_type`` — the number is only a label.
"""

from lambda_erp.accounting.setup import spine
from lambda_erp.accounting.setup.pack import LocalizationPack, register_pack
from lambda_erp.accounting.setup.packs.de_common import (
    make_de_setup_tax, STANDARD_RATE, REDUCED_RATE,
)


# ---------------------------------------------------------------------------
# Base chart — SKR03 (curated core). Numbers live in the account name (the
# Account doctype is name-keyed, no separate code column). root_type /
# report_type inherit from the parent; set on the five roots. Organised by
# balance-sheet section (root_type), NOT by the leading SKR03 digit.
# ---------------------------------------------------------------------------
DE_SKR03_CHART = {
    "0-1 Aktiva": {
        "root_type": "Asset",
        "report_type": "Balance Sheet",
        "children": {
            "0 Anlagevermögen": {
                "children": {
                    "0200 Technische Anlagen und Maschinen": {"account_type": "Fixed Asset"},
                    "0300 Andere Anlagen, Betriebs- und Geschäftsausstattung": {"account_type": "Fixed Asset"},
                    "0320 Pkw": {"account_type": "Fixed Asset"},
                    "0350 Lkw": {"account_type": "Fixed Asset"},
                    "0400 Betriebsausstattung": {"account_type": "Fixed Asset"},
                    "0420 Büroeinrichtung": {"account_type": "Fixed Asset"},
                    "0250 Wertberichtigungen zu Sachanlagen": {"account_type": "Accumulated Depreciation"},
                },
            },
            "1 Finanz- und Umlaufvermögen": {
                "children": {
                    "1000 Kasse": {"account_type": "Cash"},
                    "1200 Bank": {"account_type": "Bank"},
                    "1400 Forderungen aus Lieferungen und Leistungen": {"account_type": "Receivable"},
                    "1460 Geldtransit": {"account_type": ""},
                    "1571 Abziehbare Vorsteuer 7 %": {"account_type": ""},
                    "1576 Abziehbare Vorsteuer 19 %": {"account_type": ""},
                    "0980 Aktive Rechnungsabgrenzung": {"account_type": ""},
                    "3980 Waren (Bestand)": {"account_type": "Stock"},
                    "7000 Unfertige Erzeugnisse (Bestand)": {"account_type": "Stock"},
                },
            },
        },
    },
    "08 Eigenkapital": {
        "root_type": "Equity",
        "report_type": "Balance Sheet",
        "children": {
            "0800 Gezeichnetes Kapital / Eigenkapital": {"account_type": ""},
            "0840 Kapitalrücklage": {"account_type": ""},
            "0860 Gewinnvortrag vor Verwendung": {"account_type": ""},
            "0868 Verlustvortrag vor Verwendung": {"account_type": ""},
            "9000 Saldenvorträge, Sachkonten (Eröffnungsbilanz)": {"account_type": ""},
        },
    },
    "16-17 Verbindlichkeiten": {
        "root_type": "Liability",
        "report_type": "Balance Sheet",
        "children": {
            "16 Kurzfristige Verbindlichkeiten": {
                "children": {
                    "1600 Verbindlichkeiten aus Lieferungen und Leistungen": {"account_type": "Payable"},
                    "1608 Wareneingänge ohne Rechnung": {"account_type": "Stock Received But Not Billed"},
                    "1740 Verbindlichkeiten aus Lohn und Gehalt": {"account_type": ""},
                    "1742 Verbindlichkeiten im Rahmen der sozialen Sicherheit": {"account_type": ""},
                    "1771 Umsatzsteuer 7 %": {"account_type": "Tax"},
                    "1776 Umsatzsteuer 19 %": {"account_type": "Tax"},
                    "0990 Passive Rechnungsabgrenzung": {"account_type": ""},
                },
            },
            "17 Langfristige Verbindlichkeiten": {
                "children": {
                    "0630 Verbindlichkeiten gegenüber Kreditinstituten": {"account_type": ""},
                    "0700 Verbindlichkeiten aus Darlehen": {"account_type": ""},
                },
            },
        },
    },
    "8 Umsatzerlöse und betriebliche Erträge": {
        "root_type": "Income",
        "report_type": "Profit and Loss",
        "children": {
            "8200 Erlöse": {"account_type": "Income Account"},
            "8300 Erlöse 7 % USt": {"account_type": "Income Account"},
            "8400 Erlöse 19 % USt": {"account_type": "Income Account"},
            "8700 Erlösschmälerungen (Skonti, Rabatte)": {"account_type": ""},
            "2700 Sonstige betriebliche Erträge": {"account_type": ""},
            "2660 Erträge aus Währungsumrechnung": {"account_type": ""},
        },
    },
    "2-4 Aufwendungen": {
        "root_type": "Expense",
        "report_type": "Profit and Loss",
        "children": {
            "3 Wareneingang und Materialaufwand": {
                "children": {
                    "3200 Wareneingang": {"account_type": "Cost of Goods Sold"},
                    "3300 Wareneingang 7 % Vorsteuer": {"account_type": "Cost of Goods Sold"},
                    "3400 Wareneingang 19 % Vorsteuer": {"account_type": "Cost of Goods Sold"},
                    "3030 Einkauf Roh-, Hilfs- und Betriebsstoffe": {"account_type": "Cost of Goods Sold"},
                    "3800 Bezugsnebenkosten": {"account_type": "Chargeable"},
                    "3830 Zölle und Einfuhrabgaben": {"account_type": "Chargeable"},
                    "3960 Bestandsveränderungen": {"account_type": "Stock Adjustment"},
                },
            },
            "4 Betriebliche Aufwendungen": {
                "children": {
                    "4100 Löhne und Gehälter": {"account_type": ""},
                    "4130 Gesetzliche soziale Aufwendungen": {"account_type": ""},
                    "4200 Raumkosten": {"account_type": ""},
                    "4210 Miete (unbewegliche Wirtschaftsgüter)": {"account_type": ""},
                    "4360 Versicherungen": {"account_type": ""},
                    "4500 Fahrzeugkosten": {"account_type": ""},
                    "4600 Werbekosten": {"account_type": ""},
                    "4660 Reisekosten": {"account_type": ""},
                    "4910 Porto, Telefon und Bürobedarf": {"account_type": ""},
                    "4830 Abschreibungen auf Sachanlagen": {"account_type": "Depreciation"},
                    "4840 Kursdifferenzen (Währungsumrechnung)": {"account_type": ""},
                    "4845 Kursdifferenzen (nicht realisiert)": {"account_type": ""},
                    "4970 Rundungsdifferenzen": {"account_type": "Round Off"},
                },
            },
            "2 Neutrale Aufwendungen": {
                "children": {
                    "2100 Zinsen und ähnliche Aufwendungen": {"account_type": ""},
                    "2280 Steuern vom Einkommen und Ertrag": {"account_type": ""},
                },
            },
        },
    },
}


DE_SKR03_ANCHORS = {
    spine.CURRENT_ASSETS: "1 Finanz- und Umlaufvermögen",
    spine.FIXED_ASSETS: "0 Anlagevermögen",
    spine.CURRENT_LIABILITIES: "16 Kurzfristige Verbindlichkeiten",
    spine.EQUITY: "08 Eigenkapital",
    spine.INCOME: "8 Umsatzerlöse und betriebliche Erträge",
    spine.DIRECT_COSTS: "3 Wareneingang und Materialaufwand",
    spine.OPERATING_EXPENSES: "4 Betriebliche Aufwendungen",
}


# Every default the posting engine references must resolve to an SKR03 leaf, or
# postings that fall back to it will fail. Keys mirror STANDARD_DEFAULTS.
DE_SKR03_DEFAULTS = {
    "default_receivable_account": "1400 Forderungen aus Lieferungen und Leistungen",
    "default_payable_account": "1600 Verbindlichkeiten aus Lieferungen und Leistungen",
    "default_income_account": "8400 Erlöse 19 % USt",
    "default_expense_account": "3400 Wareneingang 19 % Vorsteuer",
    "round_off_account": "4970 Rundungsdifferenzen",
    "stock_in_hand_account": "3980 Waren (Bestand)",
    "stock_received_but_not_billed": "1608 Wareneingänge ohne Rechnung",
    "stock_adjustment_account": "3960 Bestandsveränderungen",
    "default_opening_balance_equity": "9000 Saldenvorträge, Sachkonten (Eröffnungsbilanz)",
    "default_freight_in_account": "3800 Bezugsnebenkosten",
    "default_customs_account": "3830 Zölle und Einfuhrabgaben",
    "default_exchange_gain_loss_account": "4840 Kursdifferenzen (Währungsumrechnung)",
    "default_unrealized_exchange_account": "4845 Kursdifferenzen (nicht realisiert)",
}


# German VAT wiring — (title, rate, account_leaf). Output tax = Umsatzsteuer
# (liability), input tax = Vorsteuer (recoverable asset).
_SALES_TAXES = [
    ("Umsatzsteuer 19 % (Regelsteuersatz)", STANDARD_RATE, "1776 Umsatzsteuer 19 %"),
    ("Umsatzsteuer 7 % (ermäßigt)", REDUCED_RATE, "1771 Umsatzsteuer 7 %"),
]
_PURCHASE_TAXES = [
    ("Vorsteuer 19 %", STANDARD_RATE, "1576 Abziehbare Vorsteuer 19 %"),
    ("Vorsteuer 7 %", REDUCED_RATE, "1571 Abziehbare Vorsteuer 7 %"),
]

de_skr03_setup_tax = make_de_setup_tax(_SALES_TAXES, _PURCHASE_TAXES)


DE_SKR03_PACK = register_pack(LocalizationPack(
    country="de",
    variant="skr03",
    label="Germany (DATEV SKR03)",
    currency="EUR",
    language="de",
    base_chart=DE_SKR03_CHART,
    anchors=DE_SKR03_ANCHORS,
    defaults=DE_SKR03_DEFAULTS,
    setup_tax=de_skr03_setup_tax,
    notes=(
        "German SME chart, DATEV SKR03 (process-ordered), German titles with "
        "SKR03 numbers, EUR. Umsatzsteuer/Vorsteuer templates at 19 / 7 %. "
        "Hand-authored from the published standard, cross-checked against Odoo "
        "l10n_de. The most widely used German chart — the default for a bare `de`."
    ),
))
