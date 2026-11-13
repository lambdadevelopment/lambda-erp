"""
Tax and totals calculation engine.

Runs on every transaction (quotation, order, invoice) and supports several
charge types:
- "On Net Total": percentage of net total
- "On Previous Row Amount": percentage of a previous tax row
- "On Previous Row Total": percentage of cumulative total at a previous row
- "Actual": fixed amount
- "On Item Quantity": fixed amount per unit

Taxes can be inclusive (included in the printed price) or exclusive (added on top).

Rounding
--------
Amounts are calculated as decimal numbers and rounded half-up (away from
zero) to the cent, at these points and nowhere in between:

- a line: rate x qty -> amount, once per line;
- a tax row: the tax of all lines is added up unrounded, then the row is
  rounded once (3 x 31.995 = 95.985 -> 95.99, not 3 x 32.00 and not 95.98);
- the totals are sums of those rounded amounts;
- base-currency amounts are the rounded document amounts times the
  conversion rate, rounded once.

A return is the same calculation with negative quantities and gives exactly
the negative amounts. `rounded_total` is the grand total rounded half-up to
whole currency units, with `rounding_adjustment` the difference; both are
informational: nothing is posted, owed or paid from them.
"""

import json
from decimal import Decimal
from lambda_erp.utils import flt, cint, _dict, dec, money
from lambda_erp.exceptions import ValidationError

HUNDRED = Decimal(100)

class TaxCalculator:
    """Calculate taxes and totals for a transaction document.

    This is a direct port of the reference implementation's `calculate_taxes_and_totals` class.
    The document must have:
    - items: list of line items with qty, rate, amount, etc.
    - taxes: list of tax rows with charge_type, rate, account_head, etc.
    - conversion_rate: currency conversion rate
    - discount_amount, apply_discount_on: optional discount fields
    """

    def __init__(self, doc):
        self.doc = doc

    def calculate(self):
        """Main calculation entry point."""
        items = self.doc.get("items") or []
        if not items:
            return

        self.discount_amount_applied = False
        self._calculate()

        if self.doc.get("discount_amount"):
            self.apply_discount_amount()

    def _calculate(self):
        self.calculate_item_values()
        self.initialize_taxes()
        self.determine_exclusive_rate()
        self.calculate_net_total()
        self.calculate_taxes()
        self.calculate_totals()

    def calculate_item_values(self):
        """Calculate rate, amount, net_rate, net_amount for each line item.

        - Discount percentage and discount amount
        - Price list rate -> discounted rate
        - Base currency conversion
        """
        conversion_rate = dec(self.doc.get("conversion_rate") or 1.0)

        for item in self.doc.get("items"):
            # A line ordered in a further unit (units.py) carries its price
            # per stock unit: 12.99 per carton of 100 is 0.1299 a piece.
            # Rounding that to the cent would move the line amount, so the
            # unit rate keeps six places there; amounts round as always.
            places = 6 if flt(item.get("conversion_factor")) not in (0.0, 1.0) else 2
            # Apply discount to get rate from price_list_rate
            if item.get('rate') is not None and flt(item['rate']) == 0:
                item['rate'] = 0.0
            elif flt(item.get("discount_percentage")) == 100:
                item["rate"] = 0.0
            elif item.get("price_list_rate") is not None and (flt(item['price_list_rate']) != 0 or item.get('rate') is None):
                list_rate, percent = dec(item["price_list_rate"]), dec(item.get("discount_percentage"))
                if item.get("rate") is None or percent > 0:
                    item["rate"] = money(list_rate * (1 - percent / HUNDRED), places)
                    item["discount_amount"] = money(list_rate * percent / HUNDRED, places)
                elif item.get("discount_amount"):
                    item["rate"] = float(list_rate - dec(item["discount_amount"]))

            rate = dec(item.get("rate"))
            # Set net_rate = rate (before inclusive tax adjustment)
            item["net_rate"] = money(rate, places)

            # Calculate amount: the decimal product, rounded once
            item["amount"] = money(rate * dec(item.get("qty", 0)))
            item["net_amount"] = item["amount"]

            # Set base currency values
            item["base_rate"] = money(rate * conversion_rate)
            item["base_amount"] = money(dec(item["amount"]) * conversion_rate)
            item["base_net_rate"] = money(dec(item["net_rate"]) * conversion_rate)
            item["base_net_amount"] = money(dec(item["net_amount"]) * conversion_rate)

    def initialize_taxes(self):
        """Reset tax computation fields before recalculating.

        For `charge_type = "Actual"` rows (freight, shipping, customs, any
        fixed-amount charge), `tax_amount` IS the user-provided input, not
        a derived value — zeroing it here would wipe the freight amount on
        every save and leave the Actual-charge path with nothing to
        distribute. Only reset derived fields for those rows.
        """
        derived_fields = [
            "total",
            "tax_amount_for_current_item",
            "grand_total_for_current_item",
            "tax_fraction_for_current_item",
            "grand_total_fraction_for_current_item",
        ]
        for tax in self.doc.get("taxes") or []:
            if tax.get("charge_type") != "Actual":
                tax["tax_amount"] = 0.0
            for field in derived_fields:
                tax[field] = 0.0

    def determine_exclusive_rate(self):
        """Adjust net_rate/net_amount for taxes included in the printed price.

        This is the "tax-inclusive pricing" logic from the reference implementation. When a tax is
        marked as included_in_print_rate, the item's net_amount is reduced so
        that net_amount + tax = original amount.
        """
        taxes = self.doc.get("taxes") or []
        if not any(cint(tax.get("included_in_print_rate")) for tax in taxes):
            return

        conversion_rate = dec(self.doc.get("conversion_rate") or 1.0)
        for item in self.doc.get("items"):
            item_tax_map = self._load_item_tax_rate(item.get("item_tax_rate"))
            cumulated_tax_fraction = Decimal(0)
            total_inclusive_tax_amount_per_qty = Decimal(0)
            fractions = []  # per tax row: (tax fraction, grand total fraction), exact

            for i, tax in enumerate(taxes):
                tax_fraction, inclusive_amount_per_qty = self._get_current_tax_fraction(
                    tax, item_tax_map, i, fractions
                )
                grand_fraction = (1 + tax_fraction) if i == 0 else fractions[i - 1][1] + tax_fraction
                fractions.append((tax_fraction, grand_fraction))
                tax["tax_fraction_for_current_item"] = float(tax_fraction)
                tax["grand_total_fraction_for_current_item"] = float(grand_fraction)

                cumulated_tax_fraction += tax_fraction
                total_inclusive_tax_amount_per_qty += inclusive_amount_per_qty * dec(item.get("qty"))

            if item.get("qty") and (cumulated_tax_fraction or total_inclusive_tax_amount_per_qty):
                amount = dec(item["amount"]) - total_inclusive_tax_amount_per_qty
                item["net_amount"] = money(amount / (1 + cumulated_tax_fraction))
                item["net_rate"] = money(dec(item["net_amount"]) / dec(item["qty"]))

                item["base_net_rate"] = money(dec(item["net_rate"]) * conversion_rate)
                item["base_net_amount"] = money(dec(item["net_amount"]) * conversion_rate)

    def _get_current_tax_fraction(self, tax, item_tax_map, idx, fractions=()):
        """Get the tax fraction for back-calculating exclusive rate from inclusive price.

        `fractions` are the exact (tax, grand total) fractions of the rows
        before this one, for rows that refer to a previous row."""
        current_tax_fraction = Decimal(0)
        inclusive_tax_amount_per_qty = Decimal(0)

        if cint(tax.get("included_in_print_rate")):
            tax_rate = self._get_tax_rate(tax, item_tax_map)
            charge_type = tax.get("charge_type", "On Net Total")

            if charge_type == "On Net Total":
                current_tax_fraction = tax_rate / HUNDRED
            elif charge_type == "On Previous Row Amount":
                row_id = cint(tax.get("row_id", 0)) - 1
                if 0 <= row_id < len(fractions):
                    current_tax_fraction = (tax_rate / HUNDRED) * fractions[row_id][0]
            elif charge_type == "On Previous Row Total":
                row_id = cint(tax.get("row_id", 0)) - 1
                if 0 <= row_id < len(fractions):
                    current_tax_fraction = (tax_rate / HUNDRED) * fractions[row_id][1]
            elif charge_type == "On Item Quantity":
                inclusive_tax_amount_per_qty = tax_rate

        return current_tax_fraction, inclusive_tax_amount_per_qty

    def _get_tax_rate(self, tax, item_tax_map):
        """Get tax rate (a Decimal), checking item-specific overrides first."""
        account_head = tax.get("account_head", "")
        if account_head in item_tax_map:
            return dec(item_tax_map[account_head])
        return dec(tax.get("rate", 0))

    def _load_item_tax_rate(self, item_tax_rate):
        """Parse item_tax_rate JSON string to dict."""
        if not item_tax_rate:
            return {}
        if isinstance(item_tax_rate, str):
            try:
                return json.loads(item_tax_rate)
            except (json.JSONDecodeError, ValueError):
                return {}
        return item_tax_rate if isinstance(item_tax_rate, dict) else {}

    def calculate_net_total(self):
        """Sum up item amounts to get document totals."""
        doc = self.doc
        sums = {field: Decimal(0) for field in ("total_qty", "total", "base_total", "net_total", "base_net_total")}
        for item in doc.get("items"):
            sums["total_qty"] += dec(item.get("qty", 0))
            sums["total"] += dec(item.get("amount", 0))
            sums["base_total"] += dec(item.get("base_amount", 0))
            sums["net_total"] += dec(item.get("net_amount", 0))
            sums["base_net_total"] += dec(item.get("base_net_amount", 0))

        doc["total_qty"] = float(sums["total_qty"])
        for field in ["total", "base_total", "net_total", "base_net_total"]:
            doc[field] = money(sums[field])

    def calculate_taxes(self):
        """Calculate tax amounts row by row, item by item.

        This is the core tax calculation loop from the reference implementation. For each item,
        it walks through each tax row and computes the tax amount based on
        the charge_type (On Net Total, On Previous Row Amount, Actual, etc.).

        The running total accumulates so each subsequent tax row can reference
        the cumulative total from previous rows. The per-item amounts stay
        exact decimals; each tax row is rounded once, after all items.
        """
        taxes = self.doc.get("taxes") or []
        items = self.doc.get("items") or []
        if not taxes:
            return

        # For "Actual" charge type, distribute evenly across items
        actual_left = {i: dec(tax.get("tax_amount", 0)) for i, tax in enumerate(taxes)
                       if tax.get("charge_type") == "Actual"}
        accumulated = [Decimal(0)] * len(taxes)

        for n, item in enumerate(items):
            item_tax_map = self._load_item_tax_rate(item.get("item_tax_rate"))
            current = []       # this item's exact tax per row
            running = []       # this item's exact running total per row

            for i, tax in enumerate(taxes):
                amount = self._get_current_tax_amount(item, tax, item_tax_map, current, running)

                # Adjust divisional loss to the last item (the reference implementation pattern)
                if i in actual_left:
                    actual_left[i] -= amount
                    if n == len(items) - 1:
                        amount += actual_left[i]
                else:
                    accumulated[i] += amount

                current.append(amount)
                running.append((dec(item.get("net_amount", 0)) if i == 0 else running[i - 1]) + amount)
                tax["tax_amount_for_current_item"] = float(amount)
                tax["grand_total_for_current_item"] = float(running[i])

        # Round each tax row once, then set the cumulative totals
        conversion_rate = dec(self.doc.get("conversion_rate") or 1.0)
        for i, tax in enumerate(taxes):
            tax["tax_amount"] = money(tax.get("tax_amount", 0)) if i in actual_left else money(accumulated[i])
            previous = dec(self.doc.get("net_total", 0)) if i == 0 else dec(taxes[i - 1]["total"])
            tax["total"] = money(previous + dec(tax["tax_amount"]))

            # Base currency
            tax["base_tax_amount"] = money(dec(tax["tax_amount"]) * conversion_rate)
            tax["base_total"] = money(dec(tax["total"]) * conversion_rate)

    def _get_current_tax_amount(self, item, tax, item_tax_map, current=(), running=()):
        """Compute tax for one item + one tax row, as an exact Decimal.

        `current` and `running` are this item's exact tax amounts and running
        totals of the rows before this one."""
        tax_rate = self._get_tax_rate(tax, item_tax_map)
        charge_type = tax.get("charge_type", "On Net Total")
        net_amount = dec(item.get("net_amount", 0))

        if charge_type == "Actual":
            # Distribute actual amount proportionally across items
            total = dec(self.doc.get("net_total")) or Decimal(1)
            return dec(tax.get("tax_amount", 0)) * net_amount / total

        elif charge_type == "On Net Total":
            return net_amount * tax_rate / HUNDRED

        elif charge_type == "On Previous Row Amount":
            row_id = cint(tax.get("row_id", 0)) - 1
            if 0 <= row_id < len(current):
                return current[row_id] * tax_rate / HUNDRED
            return Decimal(0)

        elif charge_type == "On Previous Row Total":
            row_id = cint(tax.get("row_id", 0)) - 1
            if 0 <= row_id < len(running):
                return running[row_id] * tax_rate / HUNDRED
            return Decimal(0)

        elif charge_type == "On Item Quantity":
            return dec(item.get("qty", 0)) * tax_rate

        return Decimal(0)

    def calculate_totals(self):
        """Calculate grand_total, rounded_total, etc."""
        doc = self.doc
        taxes = doc.get("taxes") or []

        if taxes:
            doc["grand_total"] = money(taxes[-1]["total"])
            doc["total_taxes_and_charges"] = money(dec(doc["grand_total"]) - dec(doc["net_total"]))
        else:
            doc["grand_total"] = money(doc["net_total"])
            doc["total_taxes_and_charges"] = 0

        conversion_rate = dec(doc.get("conversion_rate") or 1.0)
        doc["base_grand_total"] = money(dec(doc["grand_total"]) * conversion_rate)
        doc["base_total_taxes_and_charges"] = money(dec(doc["total_taxes_and_charges"]) * conversion_rate)

        self._set_rounded_total()

    def _set_rounded_total(self):
        """The grand total in whole currency units, half-up, and what that
        differs by. Informational: nothing is posted or owed from it."""
        doc = self.doc
        doc["rounded_total"] = money(doc["grand_total"], 0)
        doc["rounding_adjustment"] = money(dec(doc["rounded_total"]) - dec(doc["grand_total"]))

    def apply_discount_amount(self):
        """Apply a flat discount amount to the document."""
        discount = dec(self.doc.get("discount_amount"))
        if not discount:
            return

        if self.doc.get("apply_discount_on") == "Net Total":
            # Distribute discount across items proportionally
            net_total = dec(self.doc.get("net_total")) or Decimal(1)
            for item in self.doc.get("items"):
                item_discount = dec(money(discount * dec(item.get("net_amount", 0)) / net_total))
                item["net_amount"] = float(dec(item["net_amount"]) - item_discount)
                if flt(item.get("qty")):
                    item["net_rate"] = money(dec(item["net_amount"]) / dec(item["qty"]))

            self.discount_amount_applied = True
            self._calculate()
        else:
            # Discount on Grand Total - subtract from grand total
            self.doc["grand_total"] = money(dec(self.doc["grand_total"]) - discount)
            self.doc["base_grand_total"] = money(
                dec(self.doc["grand_total"]) * dec(self.doc.get("conversion_rate") or 1.0))
            self._set_rounded_total()

def calculate_taxes_and_totals(doc):
    """Convenience function matching the reference implementation's pattern."""
    TaxCalculator(doc).calculate()


# --- Billing frequency split (offers with recurring lines) -----------------
#
# A line item may carry a `frequency`: one-time ("Einmalig") or a recurring
# cadence. On an offer the one-time and recurring lines are totalled
# separately — a recurring line must not inflate the one-time grand total —
# and each recurring period shows its own net / tax / grand. These helpers run
# the SAME tax engine on each frequency group independently so every period
# carries correct taxes; nothing here changes the shared calculation above.

ONE_TIME_FREQUENCY = "One-time"
# Recurring cadences in display order. These match the Subscription doctype's
# billing_interval values exactly, so a recurring offer line maps 1:1 onto a
# Subscription later. (Templates localize these labels for display.)
RECURRING_FREQUENCY_ORDER = ["Monthly", "Quarterly", "Half-Yearly", "Yearly"]


def _item_frequency(item):
    return (item.get("frequency") or ONE_TIME_FREQUENCY)


def has_recurring_items(doc):
    """True if any line item carries a recurring (non-Einmalig) frequency."""
    return any(_item_frequency(it) != ONE_TIME_FREQUENCY for it in (doc.get("items") or []))


def split_by_frequency(doc):
    """Group a doc's items by `frequency` and total each group independently.

    Returns `(one_time, recurring)` where `one_time` is a totals dict for the
    Einmalig group and `recurring` is an ordered list of
    `{frequency, net_total, total_taxes_and_charges, grand_total}` — one per
    recurring period present. Each group is run through the standard tax engine
    with the doc's tax rows and conversion rate, so taxes are correct per
    period. (Document-level discounts are not distributed across groups.)
    """
    import copy

    items = doc.get("items") or []
    taxes = doc.get("taxes") or []
    conversion_rate = doc.get("conversion_rate") or 1.0

    groups = {}
    for it in items:
        groups.setdefault(_item_frequency(it), []).append(it)

    def totals_for(group_items):
        tmp = {
            "items": copy.deepcopy(group_items),
            "taxes": copy.deepcopy(taxes),
            "conversion_rate": conversion_rate,
        }
        calculate_taxes_and_totals(tmp)
        return {
            "net_total": flt(tmp.get("net_total"), 2),
            "total_taxes_and_charges": flt(tmp.get("total_taxes_and_charges"), 2),
            "grand_total": flt(tmp.get("grand_total"), 2),
        }

    one_time = totals_for(groups.get(ONE_TIME_FREQUENCY, []))

    recurring, seen = [], set()
    for freq in RECURRING_FREQUENCY_ORDER:
        if groups.get(freq):
            recurring.append({"frequency": freq, **totals_for(groups[freq])})
            seen.add(freq)
    # Any custom/unknown recurring label, kept in insertion order after the known ones.
    for freq, gitems in groups.items():
        if freq != ONE_TIME_FREQUENCY and freq not in seen:
            recurring.append({"frequency": freq, **totals_for(gitems)})

    return one_time, recurring
