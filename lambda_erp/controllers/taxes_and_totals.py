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
"""

import json
from lambda_erp.utils import flt, cint, _dict
from lambda_erp.exceptions import ValidationError

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
        conversion_rate = flt(self.doc.get("conversion_rate") or 1.0)

        for item in self.doc.get("items"):
            # Apply discount to get rate from price_list_rate
            if flt(item.get("discount_percentage")) == 100:
                item["rate"] = 0.0
            elif item.get("price_list_rate"):
                if not item.get("rate") or flt(item.get("discount_percentage")) > 0:
                    item["rate"] = flt(
                        flt(item["price_list_rate"]) * (1.0 - flt(item.get("discount_percentage")) / 100.0),
                        2,
                    )
                    item["discount_amount"] = flt(
                        flt(item["price_list_rate"]) * flt(item.get("discount_percentage")) / 100.0, 2
                    )
                elif item.get("discount_amount"):
                    item["rate"] = flt(item["price_list_rate"]) - flt(item["discount_amount"])

            # Set net_rate = rate (before inclusive tax adjustment)
            item["net_rate"] = flt(item.get("rate"), 2)

            # Calculate amount
            qty = flt(item.get("qty", 0))
            item["amount"] = flt(flt(item.get("rate")) * qty, 2)
            item["net_amount"] = flt(item["amount"], 2)

            # Set base currency values
            item["base_rate"] = flt(flt(item.get("rate")) * conversion_rate, 2)
            item["base_amount"] = flt(flt(item["amount"]) * conversion_rate, 2)
            item["base_net_rate"] = flt(flt(item["net_rate"]) * conversion_rate, 2)
            item["base_net_amount"] = flt(flt(item["net_amount"]) * conversion_rate, 2)

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

        for item in self.doc.get("items"):
            item_tax_map = self._load_item_tax_rate(item.get("item_tax_rate"))
            cumulated_tax_fraction = 0
            total_inclusive_tax_amount_per_qty = 0

            for i, tax in enumerate(taxes):
                tax_fraction, inclusive_amount_per_qty = self._get_current_tax_fraction(
                    tax, item_tax_map, i
                )
                tax["tax_fraction_for_current_item"] = tax_fraction

                if i == 0:
                    tax["grand_total_fraction_for_current_item"] = 1 + tax_fraction
                else:
                    tax["grand_total_fraction_for_current_item"] = (
                        taxes[i - 1].get("grand_total_fraction_for_current_item", 0) + tax_fraction
                    )

                cumulated_tax_fraction += tax_fraction
                total_inclusive_tax_amount_per_qty += inclusive_amount_per_qty * flt(item.get("qty"))

            if item.get("qty") and (cumulated_tax_fraction or total_inclusive_tax_amount_per_qty):
                amount = flt(item["amount"]) - total_inclusive_tax_amount_per_qty
                item["net_amount"] = flt(amount / (1 + cumulated_tax_fraction), 2)
                item["net_rate"] = flt(item["net_amount"] / flt(item["qty"]), 2)

                conversion_rate = flt(self.doc.get("conversion_rate") or 1.0)
                item["base_net_rate"] = flt(item["net_rate"] * conversion_rate, 2)
                item["base_net_amount"] = flt(item["net_amount"] * conversion_rate, 2)

    def _get_current_tax_fraction(self, tax, item_tax_map, idx):
        """Get the tax fraction for back-calculating exclusive rate from inclusive price."""
        current_tax_fraction = 0
        inclusive_tax_amount_per_qty = 0

        if cint(tax.get("included_in_print_rate")):
            tax_rate = self._get_tax_rate(tax, item_tax_map)
            charge_type = tax.get("charge_type", "On Net Total")
            taxes = self.doc.get("taxes") or []

            if charge_type == "On Net Total":
                current_tax_fraction = tax_rate / 100.0
            elif charge_type == "On Previous Row Amount":
                row_id = cint(tax.get("row_id", 0)) - 1
                if 0 <= row_id < len(taxes):
                    current_tax_fraction = (tax_rate / 100.0) * taxes[row_id].get(
                        "tax_fraction_for_current_item", 0
                    )
            elif charge_type == "On Previous Row Total":
                row_id = cint(tax.get("row_id", 0)) - 1
                if 0 <= row_id < len(taxes):
                    current_tax_fraction = (tax_rate / 100.0) * taxes[row_id].get(
                        "grand_total_fraction_for_current_item", 0
                    )
            elif charge_type == "On Item Quantity":
                inclusive_tax_amount_per_qty = flt(tax_rate)

        return current_tax_fraction, inclusive_tax_amount_per_qty

    def _get_tax_rate(self, tax, item_tax_map):
        """Get tax rate, checking item-specific overrides first."""
        account_head = tax.get("account_head", "")
        if account_head in item_tax_map:
            return flt(item_tax_map[account_head])
        return flt(tax.get("rate", 0))

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
        doc["total_qty"] = 0
        doc["total"] = 0
        doc["base_total"] = 0
        doc["net_total"] = 0
        doc["base_net_total"] = 0

        for item in doc.get("items"):
            doc["total_qty"] = flt(doc["total_qty"]) + flt(item.get("qty", 0))
            doc["total"] = flt(doc["total"]) + flt(item.get("amount", 0))
            doc["base_total"] = flt(doc["base_total"]) + flt(item.get("base_amount", 0))
            doc["net_total"] = flt(doc["net_total"]) + flt(item.get("net_amount", 0))
            doc["base_net_total"] = flt(doc["base_net_total"]) + flt(item.get("base_net_amount", 0))

        for field in ["total", "base_total", "net_total", "base_net_total"]:
            doc[field] = flt(doc[field], 2)

    def calculate_taxes(self):
        """Calculate tax amounts row by row, item by item.

        This is the core tax calculation loop from the reference implementation. For each item,
        it walks through each tax row and computes the tax amount based on
        the charge_type (On Net Total, On Previous Row Amount, Actual, etc.).

        The running total accumulates so each subsequent tax row can reference
        the cumulative total from previous rows.
        """
        taxes = self.doc.get("taxes") or []
        items = self.doc.get("items") or []
        if not taxes:
            return

        # For "Actual" charge type, distribute evenly across items
        actual_tax_dict = {}
        for tax in taxes:
            if tax.get("charge_type") == "Actual":
                actual_tax_dict[tax.get("idx", 0)] = flt(tax.get("tax_amount", 0))

        for n, item in enumerate(items):
            item_tax_map = self._load_item_tax_rate(item.get("item_tax_rate"))

            for i, tax in enumerate(taxes):
                current_tax_amount = self._get_current_tax_amount(item, tax, item_tax_map)

                # Adjust divisional loss to the last item (the reference implementation pattern)
                if tax.get("charge_type") == "Actual":
                    idx = tax.get("idx", 0)
                    actual_tax_dict[idx] = actual_tax_dict.get(idx, 0) - current_tax_amount
                    if n == len(items) - 1:
                        current_tax_amount += actual_tax_dict.get(idx, 0)

                # Accumulate tax amount
                if tax.get("charge_type") != "Actual":
                    tax["tax_amount"] = flt(tax.get("tax_amount", 0)) + current_tax_amount

                tax["tax_amount_for_current_item"] = current_tax_amount

                # Build running grand total for this item
                if i == 0:
                    tax["grand_total_for_current_item"] = flt(item.get("net_amount", 0)) + current_tax_amount
                else:
                    tax["grand_total_for_current_item"] = flt(
                        taxes[i - 1].get("grand_total_for_current_item", 0)
                    ) + current_tax_amount

        # Set cumulative totals on each tax row
        for i, tax in enumerate(taxes):
            tax["tax_amount"] = flt(tax.get("tax_amount", 0), 2)
            if i == 0:
                tax["total"] = flt(self.doc.get("net_total", 0)) + tax["tax_amount"]
            else:
                tax["total"] = flt(taxes[i - 1]["total"]) + tax["tax_amount"]

            tax["total"] = flt(tax["total"], 2)

            # Base currency
            conversion_rate = flt(self.doc.get("conversion_rate") or 1.0)
            tax["base_tax_amount"] = flt(tax["tax_amount"] * conversion_rate, 2)
            tax["base_total"] = flt(tax["total"] * conversion_rate, 2)

    def _get_current_tax_amount(self, item, tax, item_tax_map):
        """Compute tax for one item + one tax row."""
        tax_rate = self._get_tax_rate(tax, item_tax_map)
        charge_type = tax.get("charge_type", "On Net Total")
        taxes = self.doc.get("taxes") or []

        if charge_type == "Actual":
            # Distribute actual amount proportionally across items
            total = flt(self.doc.get("net_total")) or 1
            proportion = flt(item.get("net_amount", 0)) / total
            return flt(tax.get("tax_amount", 0)) * proportion

        elif charge_type == "On Net Total":
            return flt(item.get("net_amount", 0)) * tax_rate / 100.0

        elif charge_type == "On Previous Row Amount":
            row_id = cint(tax.get("row_id", 0)) - 1
            if 0 <= row_id < len(taxes):
                return flt(taxes[row_id].get("tax_amount_for_current_item", 0)) * tax_rate / 100.0
            return 0

        elif charge_type == "On Previous Row Total":
            row_id = cint(tax.get("row_id", 0)) - 1
            if 0 <= row_id < len(taxes):
                return flt(taxes[row_id].get("grand_total_for_current_item", 0)) * tax_rate / 100.0
            return 0

        elif charge_type == "On Item Quantity":
            return flt(item.get("qty", 0)) * tax_rate

        return 0

    def calculate_totals(self):
        """Calculate grand_total, rounded_total, etc."""
        doc = self.doc
        taxes = doc.get("taxes") or []

        if taxes:
            doc["grand_total"] = flt(taxes[-1]["total"], 2)
            doc["total_taxes_and_charges"] = flt(doc["grand_total"]) - flt(doc["net_total"])
        else:
            doc["grand_total"] = flt(doc["net_total"], 2)
            doc["total_taxes_and_charges"] = 0

        doc["total_taxes_and_charges"] = flt(doc["total_taxes_and_charges"], 2)

        conversion_rate = flt(doc.get("conversion_rate") or 1.0)
        doc["base_grand_total"] = flt(flt(doc["grand_total"]) * conversion_rate, 2)
        doc["base_total_taxes_and_charges"] = flt(
            flt(doc["total_taxes_and_charges"]) * conversion_rate, 2
        )

        # Rounded total
        doc["rounded_total"] = round(flt(doc["grand_total"]))
        doc["rounding_adjustment"] = flt(doc["rounded_total"]) - flt(doc["grand_total"])

    def apply_discount_amount(self):
        """Apply a flat discount amount to the document."""
        discount = flt(self.doc.get("discount_amount"))
        if not discount:
            return

        if self.doc.get("apply_discount_on") == "Net Total":
            # Distribute discount across items proportionally
            net_total = flt(self.doc.get("net_total")) or 1
            for item in self.doc.get("items"):
                proportion = flt(item.get("net_amount", 0)) / net_total
                item_discount = flt(discount * proportion, 2)
                item["net_amount"] = flt(item["net_amount"]) - item_discount
                if flt(item.get("qty")):
                    item["net_rate"] = flt(item["net_amount"] / flt(item["qty"]), 2)

            self.discount_amount_applied = True
            self._calculate()
        else:
            # Discount on Grand Total - subtract from grand total
            self.doc["grand_total"] = flt(self.doc["grand_total"]) - discount
            self.doc["base_grand_total"] = flt(self.doc["grand_total"]) * flt(
                self.doc.get("conversion_rate") or 1.0
            )
            self.doc["rounded_total"] = round(flt(self.doc["grand_total"]))
            self.doc["rounding_adjustment"] = flt(self.doc["rounded_total"]) - flt(self.doc["grand_total"])

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
