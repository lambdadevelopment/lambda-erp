"""Switzerland — the Swiss KMU localization pack.

Hand-authored from the published Swiss **Kontenrahmen KMU** (the standard SME
chart, veb.ch / Sterchi-Mattle-Helbling). The account numbers and their German
titles are a Swiss functional standard, not anyone's copyrightable arrangement,
so this is authored from the standard rather than copied from any packaged data
file — same stance as the generic pack. A curated ~55-account core (not the full
250-line KMU) proportionate to the generic pack; extend as needed.

Two Swiss specifics the generic pack doesn't have:
  * **CHF** base currency, German account titles carrying their KMU number.
  * **MWST** (VAT) — the `setup_tax` hook builds Sales/Purchase tax templates at
    the current Swiss rates (Normalsatz 8.1 %, reduziert 2.6 %, Beherbergung
    3.8 %; valid from 2024-01-01) against the Umsatzsteuer / Vorsteuer accounts.
    This is the "hybrid" half of the design — rates + account wiring in Python,
    which flat data can't express.

Because the KMU base chart is richer than the generic one (it already has
1210 Rohstoffe, 1280 Angefangene Arbeiten = WIP, etc.), a sector profile
attaches *fewer* overlay accounts here — the anchors still resolve, so the same
seven profiles apply unchanged (their overlay accounts appear in English on the
German chart, which is expected: the base chart is localized, the lens is not).
"""

from lambda_erp.utils import _dict
from lambda_erp.database import get_db
from lambda_erp.accounting.chart_of_accounts import account_abbr
from lambda_erp.accounting.setup import spine
from lambda_erp.accounting.setup.pack import LocalizationPack, register_pack


# ---------------------------------------------------------------------------
# Base chart — Kontenrahmen KMU (curated core). Numbers live in the account
# name (the Account doctype is name-keyed, no separate code column). root_type /
# report_type inherit from the parent; set on the five roots.
# ---------------------------------------------------------------------------
CH_CHART = {
    "1 Aktiven": {
        "root_type": "Asset",
        "report_type": "Balance Sheet",
        "children": {
            "10 Umlaufvermögen": {
                "children": {
                    "1000 Kasse": {"account_type": "Cash"},
                    "1020 Bank": {"account_type": "Bank"},
                    "1060 Wertschriften": {"account_type": ""},
                    "1100 Forderungen aus Lieferungen und Leistungen (Debitoren)": {"account_type": "Receivable"},
                    "1109 Delkredere (Wertberichtigung Debitoren)": {"account_type": ""},
                    "1170 Vorsteuer MWST auf Material, Waren und Dienstleistungen": {"account_type": ""},
                    "1171 Vorsteuer MWST auf Investitionen und übrigem Betriebsaufwand": {"account_type": ""},
                    "1176 Verrechnungssteuer": {"account_type": ""},
                    "1200 Vorräte Handelswaren": {"account_type": "Stock"},
                    "1210 Vorräte Rohstoffe": {"account_type": "Stock"},
                    "1260 Vorräte fertige Erzeugnisse": {"account_type": "Stock"},
                    "1280 Angefangene Arbeiten": {"account_type": "Stock"},
                    "1300 Aktive Rechnungsabgrenzung": {"account_type": ""},
                },
            },
            "14 Anlagevermögen": {
                "children": {
                    "1500 Maschinen und Apparate": {"account_type": "Fixed Asset"},
                    "1510 Mobiliar und Einrichtungen": {"account_type": "Fixed Asset"},
                    "1520 Büromaschinen, Informatik, Kommunikation": {"account_type": "Fixed Asset"},
                    "1530 Fahrzeuge": {"account_type": "Fixed Asset"},
                    "1600 Immobilien": {"account_type": "Fixed Asset"},
                    "1590 Wertberichtigungen Sachanlagen": {"account_type": "Accumulated Depreciation"},
                },
            },
        },
    },
    "2 Fremdkapital": {
        "root_type": "Liability",
        "report_type": "Balance Sheet",
        "children": {
            "20 Kurzfristiges Fremdkapital": {
                "children": {
                    "2000 Verbindlichkeiten aus Lieferungen und Leistungen (Kreditoren)": {"account_type": "Payable"},
                    "2005 Noch nicht eingegangene Rechnungen (Wareneingang)": {"account_type": "Stock Received But Not Billed"},
                    "2200 Umsatzsteuer (geschuldete MWST)": {"account_type": "Tax"},
                    "2201 Verrechnungssteuer geschuldet": {"account_type": "Tax"},
                    "2270 Sozialversicherungen (AHV/IV/EO/ALV, BVG)": {"account_type": ""},
                    "2300 Passive Rechnungsabgrenzung / kurzfristige Rückstellungen": {"account_type": ""},
                },
            },
            "24 Langfristiges Fremdkapital": {
                "children": {
                    "2400 Bankdarlehen": {"account_type": ""},
                    "2451 Hypotheken": {"account_type": ""},
                },
            },
        },
    },
    "28 Eigenkapital": {
        "root_type": "Equity",
        "report_type": "Balance Sheet",
        "children": {
            "2800 Eigenkapital / Aktien- bzw. Stammkapital": {"account_type": ""},
            "2900 Gesetzliche Gewinnreserven": {"account_type": ""},
            "2970 Gewinnvortrag / Verlustvortrag": {"account_type": ""},
            "2990 Eröffnungsbilanz": {"account_type": ""},
        },
    },
    "3 Betrieblicher Ertrag aus Lieferungen und Leistungen": {
        "root_type": "Income",
        "report_type": "Profit and Loss",
        "children": {
            "3000 Produktionserlöse": {"account_type": "Income Account"},
            "3200 Handelserlöse": {"account_type": "Income Account"},
            "3400 Dienstleistungserlöse": {"account_type": "Income Account"},
            "3600 Übrige Erlöse": {"account_type": "Income Account"},
            "3800 Erlösminderungen (Skonti, Rabatte)": {"account_type": ""},
            "3805 Verluste Forderungen (Debitorenverluste)": {"account_type": ""},
        },
    },
    "Betrieblicher Aufwand": {
        "root_type": "Expense",
        "report_type": "Profit and Loss",
        "children": {
            "4 Material-, Waren- und Dienstleistungsaufwand": {
                "children": {
                    "4000 Materialaufwand Produktion": {"account_type": "Cost of Goods Sold"},
                    "4200 Handelswarenaufwand": {"account_type": "Cost of Goods Sold"},
                    "4400 Aufwand für bezogene Dienstleistungen": {"account_type": "Cost of Goods Sold"},
                    "4090 Transport- und Bezugskosten": {"account_type": "Chargeable"},
                    "4091 Zölle und Einfuhrabgaben": {"account_type": "Chargeable"},
                    "4900 Bestandesänderungen / Inventurdifferenzen": {"account_type": "Stock Adjustment"},
                },
            },
            "5 Personalaufwand": {
                "children": {
                    "5000 Lohnaufwand": {"account_type": ""},
                    "5700 Sozialversicherungsaufwand": {"account_type": ""},
                    "5800 Übriger Personalaufwand": {"account_type": ""},
                },
            },
            "6 Übriger betrieblicher Aufwand": {
                "children": {
                    "6000 Raumaufwand (Miete)": {"account_type": ""},
                    "6100 Unterhalt, Reparaturen, Ersatz": {"account_type": ""},
                    "6200 Fahrzeug- und Transportaufwand": {"account_type": ""},
                    "6300 Sachversicherungen, Abgaben, Gebühren": {"account_type": ""},
                    "6400 Energie- und Entsorgungsaufwand": {"account_type": ""},
                    "6500 Verwaltungs- und Informatikaufwand": {"account_type": ""},
                    "6600 Werbeaufwand": {"account_type": ""},
                    "6700 Sonstiger Betriebsaufwand": {"account_type": ""},
                    "6800 Abschreibungen": {"account_type": "Depreciation"},
                    "6900 Finanzaufwand": {"account_type": ""},
                    "6950 Finanzertrag": {"account_type": ""},
                    "6960 Rundungsdifferenzen": {"account_type": "Round Off"},
                    "6940 Kursdifferenzen (realisiert)": {"account_type": ""},
                    "6943 Kursdifferenzen (nicht realisiert)": {"account_type": ""},
                },
            },
            "8 Neutraler Aufwand und Ertrag": {
                "children": {
                    "8900 Direkte Steuern": {"account_type": ""},
                },
            },
        },
    },
}


CH_ANCHORS = {
    spine.CURRENT_ASSETS: "10 Umlaufvermögen",
    spine.FIXED_ASSETS: "14 Anlagevermögen",
    spine.CURRENT_LIABILITIES: "20 Kurzfristiges Fremdkapital",
    spine.EQUITY: "28 Eigenkapital",
    spine.INCOME: "3 Betrieblicher Ertrag aus Lieferungen und Leistungen",
    spine.DIRECT_COSTS: "4 Material-, Waren- und Dienstleistungsaufwand",
    spine.OPERATING_EXPENSES: "6 Übriger betrieblicher Aufwand",
}


# Every default the posting engine references must resolve to a Swiss leaf, or
# postings that fall back to it will fail. Keys mirror STANDARD_DEFAULTS.
CH_DEFAULTS = {
    "default_receivable_account": "1100 Forderungen aus Lieferungen und Leistungen (Debitoren)",
    "default_payable_account": "2000 Verbindlichkeiten aus Lieferungen und Leistungen (Kreditoren)",
    "default_income_account": "3200 Handelserlöse",
    "default_expense_account": "4200 Handelswarenaufwand",
    "round_off_account": "6960 Rundungsdifferenzen",
    "stock_in_hand_account": "1200 Vorräte Handelswaren",
    "stock_received_but_not_billed": "2005 Noch nicht eingegangene Rechnungen (Wareneingang)",
    "stock_adjustment_account": "4900 Bestandesänderungen / Inventurdifferenzen",
    "default_opening_balance_equity": "2990 Eröffnungsbilanz",
    "default_freight_in_account": "4090 Transport- und Bezugskosten",
    "default_customs_account": "4091 Zölle und Einfuhrabgaben",
    "default_exchange_gain_loss_account": "6940 Kursdifferenzen (realisiert)",
    "default_unrealized_exchange_account": "6943 Kursdifferenzen (nicht realisiert)",
}


# Current Swiss MWST rates, valid from 2024-01-01. (title, rate, account_leaf)
_SALES_VAT_ACCOUNT = "2200 Umsatzsteuer (geschuldete MWST)"
_INPUT_VAT_GOODS = "1170 Vorsteuer MWST auf Material, Waren und Dienstleistungen"
_INPUT_VAT_INVEST = "1171 Vorsteuer MWST auf Investitionen und übrigem Betriebsaufwand"

_SALES_TAXES = [
    ("MWST Normalsatz 8.1%", 8.1, _SALES_VAT_ACCOUNT),
    ("MWST reduzierter Satz 2.6%", 2.6, _SALES_VAT_ACCOUNT),
    ("MWST Beherbergung 3.8%", 3.8, _SALES_VAT_ACCOUNT),
]
_PURCHASE_TAXES = [
    ("Vorsteuer Normalsatz 8.1% (Material/DL)", 8.1, _INPUT_VAT_GOODS),
    ("Vorsteuer Normalsatz 8.1% (Investitionen)", 8.1, _INPUT_VAT_INVEST),
    ("Vorsteuer reduziert 2.6% (Material/DL)", 2.6, _INPUT_VAT_GOODS),
]


def ch_setup_tax(company_name, currency):
    """Create Swiss MWST sales/purchase tax templates. Does not commit (the
    engine owns the transaction). Returns a human summary of what was created."""
    db = get_db()
    abbr = account_abbr(company_name)
    summary = []

    def _template(title, tax_type, rate, account_leaf):
        name = f"{title} - {abbr}"
        db.insert("Tax Template", _dict(
            name=name, title=title, company=company_name, tax_type=tax_type,
        ))
        db.insert("Tax Template Detail", _dict(
            name=f"{name}-1",
            parent=name,
            charge_type="On Net Total",
            account_head=f"{account_leaf} - {abbr}",
            rate=rate,
            description=title,
            idx=1,
        ))
        summary.append(f"{tax_type}: {title} @ {rate}%")

    for title, rate, leaf in _SALES_TAXES:
        _template(title, "Sales", rate, leaf)
    for title, rate, leaf in _PURCHASE_TAXES:
        _template(title, "Purchase", rate, leaf)

    return summary


CH_PACK = register_pack(LocalizationPack(
    country="ch",
    variant=None,
    label="Switzerland (Kontenrahmen KMU)",
    currency="CHF",
    language="de",
    base_chart=CH_CHART,
    anchors=CH_ANCHORS,
    defaults=CH_DEFAULTS,
    setup_tax=ch_setup_tax,
    notes=(
        "Swiss SME chart (Kontenrahmen KMU), German titles with KMU numbers, CHF. "
        "MWST templates at 8.1 / 2.6 / 3.8 % (from 2024). Hand-authored from the "
        "published standard."
    ),
))
