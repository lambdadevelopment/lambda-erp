#!/usr/bin/env python3
"""
ERP Validation Test Suite — exercises the full ERP cycle end-to-end.

Tests all core business logic and verifies accounting integrity:
 1. Company setup with Chart of Accounts
 2. Quotation (offer to customer)
 3. Sales Order (confirmed order)
 4. Sales Invoice (financial posting with GL entries)
 5. Payment Entry (receiving payment)
 6. Purchase Order -> Purchase Invoice cycle
 7. Stock Entry (inventory movement)
 8. Journal Entry (manual bookkeeping)
 9. REGRESSION: Repeated invoice create/cancel preserves billed_qty
10. REGRESSION: Failed submit rolls back docstatus (atomicity)
11. RETURNS: Credit Note (Sales Invoice return with GL reversal)
12. RETURNS: Debit Note (Purchase Invoice return with GL reversal)
13. RETURNS: Delivery Note return (stock comes back in)

Run with: python tests/test_erp_validation.py
"""

from lambda_erp.database import setup
from lambda_erp.utils import _dict, flt, fmt_money, nowdate, add_days
from lambda_erp.accounting.chart_of_accounts import setup_chart_of_accounts, setup_cost_center
from lambda_erp.accounting.general_ledger import get_gl_balance
from lambda_erp.stock.stock_ledger import get_stock_balance


def print_header(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def print_gl_entries(db, voucher_no=None):
    """Print GL entries in a ledger format."""
    filters = {"is_cancelled": 0}
    if voucher_no:
        filters["voucher_no"] = voucher_no

    entries = db.get_all(
        "GL Entry",
        filters=filters,
        fields=["posting_date", "account", "party", "debit", "credit", "voucher_no", "remarks"],
        order_by="name",
    )
    if not entries:
        print("  (no GL entries)")
        return

    print(f"  {'Date':<12} {'Account':<35} {'Debit':>12} {'Credit':>12}  {'Voucher'}")
    print(f"  {'-'*12} {'-'*35} {'-'*12} {'-'*12}  {'-'*15}")
    for e in entries:
        party_info = f" ({e['party']})" if e.get("party") else ""
        acct = (e["account"][:32] + "..") if len(e["account"]) > 34 else e["account"]
        print(
            f"  {e['posting_date']:<12} {acct + party_info:<35} "
            f"{fmt_money(e['debit']):>12} {fmt_money(e['credit']):>12}  {e['voucher_no']}"
        )

    total_debit = sum(flt(e["debit"]) for e in entries)
    total_credit = sum(flt(e["credit"]) for e in entries)
    print(f"  {'':<12} {'TOTAL':<35} {fmt_money(total_debit):>12} {fmt_money(total_credit):>12}")


def main():
    # =====================================================================
    # SETUP
    # =====================================================================
    print_header("1. SETUP - Company, Chart of Accounts, Master Data")

    db = setup()  # In-memory SQLite

    # Create company
    db.insert("Company", _dict(
        name="Lambda Corp",
        company_name="Lambda Corp",
        default_currency="USD",
    ))

    # Create Chart of Accounts using the standard setup flow
    setup_chart_of_accounts("Lambda Corp", "USD")
    cost_center = setup_cost_center("Lambda Corp")
    print(f"  Company: Lambda Corp")
    print(f"  Cost Center: {cost_center}")

    # Count accounts created
    accounts = db.get_all("Account", filters={"company": "Lambda Corp"}, fields=["name", "root_type"])
    print(f"  Chart of Accounts: {len(accounts)} accounts created")
    for root_type in ["Asset", "Liability", "Equity", "Income", "Expense"]:
        count = len([a for a in accounts if a["root_type"] == root_type])
        print(f"    {root_type}: {count} accounts")

    # Create master data
    db.insert("Customer", _dict(name="CUST-001", customer_name="Riverside Manufacturing", customer_group="Commercial"))
    db.insert("Customer", _dict(name="CUST-002", customer_name="Summit Logistics", customer_group="Commercial"))
    db.insert("Supplier", _dict(name="SUPP-001", supplier_name="Atlas Supply Co"))
    db.insert("Supplier", _dict(name="SUPP-005", supplier_name="Ironclad Metals"))
    db.insert("Item", _dict(name="ITEM-001", item_name="Bolt Pack M8", stock_uom="Nos", standard_rate=100, is_stock_item=1))
    db.insert("Item", _dict(name="ITEM-002", item_name="Gasket Set K2", stock_uom="Nos", standard_rate=250, is_stock_item=1))
    db.insert("Item", _dict(name="SVC-001", item_name="Engineering Consultation", stock_uom="Hour", standard_rate=150, is_stock_item=0))
    db.insert("Warehouse", _dict(name="Main Warehouse - LAMB", warehouse_name="Main Warehouse", company="Lambda Corp"))
    print(f"  Master Data: 2 customers, 1 supplier, 3 items, 1 warehouse")

    # =====================================================================
    # SALES CYCLE: Quotation -> Sales Order -> Sales Invoice -> Payment
    # =====================================================================
    print_header("2. QUOTATION - Create an offer for Riverside Manufacturing")

    from lambda_erp.selling.quotation import Quotation, make_sales_order

    quotation = Quotation(
        customer="CUST-001",
        company="Lambda Corp",
        transaction_date=nowdate(),
        valid_till=add_days(nowdate(), 21).isoformat(),
        items=[
            _dict(item_code="ITEM-001", qty=10, rate=100),
            _dict(item_code="ITEM-002", qty=5, rate=250),
            _dict(item_code="SVC-001", qty=8, rate=150),
        ],
        taxes=[
            _dict(
                charge_type="On Net Total",
                account_head="Tax Payable - LAMB",
                description="Sales Tax 10%",
                rate=10,
                idx=1,
            ),
        ],
    )
    quotation.save()
    quotation.submit()

    print(f"  Quotation: {quotation.name}")
    print(f"  Customer: {quotation.customer_name}")
    print(f"  Items: {int(quotation.total_qty)} units across 3 line items")
    print(f"  Net Total:  {fmt_money(quotation.net_total, currency='USD')}")
    print(f"  Tax (10%):  {fmt_money(quotation.total_taxes_and_charges, currency='USD')}")
    print(f"  Grand Total: {fmt_money(quotation.grand_total, currency='USD')}")
    print(f"  Status: {quotation.status}")
    print(f"  (No GL entries - quotation has no financial impact)")

    # ---- Convert to Sales Order ----
    print_header("3. SALES ORDER - Convert quotation to confirmed order")

    from lambda_erp.selling.sales_order import SalesOrder, make_sales_invoice

    sales_order = make_sales_order(quotation.name)
    sales_order.delivery_date = add_days(nowdate(), 14).isoformat()
    sales_order.save()
    sales_order.submit()

    print(f"  Sales Order: {sales_order.name}")
    print(f"  Delivery Date: {sales_order.delivery_date}")
    print(f"  Grand Total: {fmt_money(sales_order.grand_total, currency='USD')}")
    print(f"  Status: {sales_order.status}")
    print(f"  (No GL entries - order has no financial impact yet)")
    print(f"  Quotation status: {db.get_value('Quotation', quotation.name, 'status')}")

    # ---- Create Sales Invoice ----
    print_header("4. SALES INVOICE - Bill the customer (GL entries created!)")

    from lambda_erp.accounting.sales_invoice import SalesInvoice

    invoice = make_sales_invoice(sales_order.name)
    invoice.save()
    invoice.submit()

    print(f"  Sales Invoice: {invoice.name}")
    print(f"  Grand Total: {fmt_money(invoice.grand_total, currency='USD')}")
    print(f"  Outstanding: {fmt_money(invoice.outstanding_amount, currency='USD')}")
    print(f"\n  GL Entries posted:")
    print_gl_entries(db, invoice.name)

    # Show account balances
    print(f"\n  Account Balances:")
    receivable = get_gl_balance("Accounts Receivable - LAMB")
    income = get_gl_balance("Sales Revenue - LAMB")
    tax = get_gl_balance("Tax Payable - LAMB")
    print(f"    Accounts Receivable:  {fmt_money(receivable)} (debit = customer owes us)")
    print(f"    Sales Revenue:       {fmt_money(income)} (credit = income earned)")
    print(f"    Tax Payable:         {fmt_money(tax)} (credit = tax we owe)")

    # ---- Receive Payment ----
    print_header("5. PAYMENT ENTRY - Receive payment from customer")

    from lambda_erp.accounting.payment_entry import PaymentEntry

    payment = PaymentEntry(
        payment_type="Receive",
        posting_date=nowdate(),
        company="Lambda Corp",
        party_type="Customer",
        party="CUST-001",
        paid_from="Accounts Receivable - LAMB",
        paid_to="Primary Bank - LAMB",
        paid_amount=flt(invoice.grand_total),
        received_amount=flt(invoice.grand_total),
        references=[
            _dict(
                reference_doctype="Sales Invoice",
                reference_name=invoice.name,
                total_amount=flt(invoice.grand_total),
                outstanding_amount=flt(invoice.outstanding_amount),
                allocated_amount=flt(invoice.grand_total),
            ),
        ],
    )
    payment.save()
    payment.submit()

    print(f"  Payment Entry: {payment.name}")
    print(f"  Amount: {fmt_money(payment.paid_amount, currency='USD')}")
    print(f"  Invoice outstanding after payment: {fmt_money(db.get_value('Sales Invoice', invoice.name, 'outstanding_amount'), currency='USD')}")
    print(f"\n  GL Entries posted:")
    print_gl_entries(db, payment.name)

    print(f"\n  Updated Account Balances:")
    print(f"    Accounts Receivable: {fmt_money(get_gl_balance('Accounts Receivable - LAMB'))} (settled!)")
    print(f"    Primary Bank:        {fmt_money(get_gl_balance('Primary Bank - LAMB'))} (cash received)")
    print(f"    Sales Revenue:      {fmt_money(get_gl_balance('Sales Revenue - LAMB'))}")

    # =====================================================================
    # PURCHASE CYCLE
    # =====================================================================
    print_header("6. PURCHASE ORDER -> PURCHASE INVOICE")

    from lambda_erp.buying.purchase_order import PurchaseOrder, make_purchase_invoice

    po = PurchaseOrder(
        supplier="SUPP-001",
        company="Lambda Corp",
        transaction_date=nowdate(),
        items=[
            _dict(item_code="ITEM-001", qty=50, rate=60, warehouse="Main Warehouse - LAMB"),
            _dict(item_code="ITEM-002", qty=20, rate=180, warehouse="Main Warehouse - LAMB"),
        ],
    )
    po.save()
    po.submit()
    print(f"  Purchase Order: {po.name} - {fmt_money(po.grand_total, currency='USD')}")

    from lambda_erp.accounting.purchase_invoice import PurchaseInvoice
    pi = make_purchase_invoice(po.name)
    pi.save()
    pi.submit()

    print(f"  Purchase Invoice: {pi.name} - {fmt_money(pi.grand_total, currency='USD')}")
    print(f"  Outstanding: {fmt_money(pi.outstanding_amount, currency='USD')}")
    print(f"\n  GL Entries posted:")
    print_gl_entries(db, pi.name)

    print(f"\n  Account Balances after purchase:")
    print(f"    Accounts Payable:    {fmt_money(get_gl_balance('Accounts Payable - LAMB'))} (credit = we owe supplier)")
    print(f"    Cost of Goods Sold:  {fmt_money(get_gl_balance('Cost of Goods Sold - LAMB'))} (debit = expense)")

    # =====================================================================
    # STOCK / INVENTORY
    # =====================================================================
    print_header("7. STOCK ENTRY - Receive materials into warehouse")

    from lambda_erp.stock.stock_entry import StockEntry

    receipt = StockEntry(
        stock_entry_type="Material Receipt",
        posting_date=nowdate(),
        company="Lambda Corp",
        to_warehouse="Main Warehouse - LAMB",
        items=[
            _dict(item_code="ITEM-001", qty=100, basic_rate=60),
            _dict(item_code="ITEM-002", qty=50, basic_rate=180),
        ],
    )
    receipt.save()
    receipt.submit()

    print(f"  Stock Entry: {receipt.name} (Material Receipt)")
    print(f"  Total Incoming Value: {fmt_money(receipt.total_incoming_value, currency='USD')}")

    # Check stock balances
    bal_a = get_stock_balance("ITEM-001", "Main Warehouse - LAMB")
    bal_b = get_stock_balance("ITEM-002", "Main Warehouse - LAMB")
    print(f"\n  Stock Balances:")
    print(f"    ITEM-001 (Bolt Pack M8): {bal_a.actual_qty} units @ {fmt_money(bal_a.valuation_rate)}/unit = {fmt_money(bal_a.stock_value)}")
    print(f"    ITEM-002 (Gasket Set K2): {bal_b.actual_qty} units @ {fmt_money(bal_b.valuation_rate)}/unit = {fmt_money(bal_b.stock_value)}")

    # Material Issue
    issue = StockEntry(
        stock_entry_type="Material Issue",
        posting_date=nowdate(),
        company="Lambda Corp",
        from_warehouse="Main Warehouse - LAMB",
        items=[
            _dict(item_code="ITEM-001", qty=10, basic_rate=60),
        ],
    )
    issue.save()
    issue.submit()

    bal_a = get_stock_balance("ITEM-001", "Main Warehouse - LAMB")
    print(f"\n  After issuing 10 units of Bolt Pack M8:")
    print(f"    ITEM-001: {bal_a.actual_qty} units remaining (stock value: {fmt_money(bal_a.stock_value)})")

    # =====================================================================
    # JOURNAL ENTRY
    # =====================================================================
    print_header("8. JOURNAL ENTRY - Manual accounting adjustment")

    from lambda_erp.accounting.journal_entry import JournalEntry

    je = JournalEntry(
        posting_date=nowdate(),
        company="Lambda Corp",
        remark="Accrue office rent for April 2026",
        accounts=[
            _dict(account="Administrative Expenses - LAMB", debit=2500, credit=0),
            _dict(account="Accounts Payable - LAMB", debit=0, credit=2500,
                  party_type="Supplier", party="SUPP-001"),
        ],
    )
    je.save()
    je.submit()

    print(f"  Journal Entry: {je.name}")
    print(f"  {je.remark}")
    print(f"\n  GL Entries posted:")
    print_gl_entries(db, je.name)

    # =====================================================================
    # REGRESSION: Repeated invoice create/cancel cycle
    # =====================================================================
    print_header("9. REGRESSION - Repeated invoice create/cancel preserves billed_qty")

    # Create a fresh Sales Order for this test
    from lambda_erp.selling.quotation import Quotation as Quotation2

    so2 = SalesOrder(
        customer="CUST-002",
        company="Lambda Corp",
        transaction_date=nowdate(),
        items=[
            _dict(item_code="ITEM-001", qty=10, rate=100),
        ],
    )
    so2.save()
    so2.submit()
    print(f"  Sales Order: {so2.name} (10 x Bolt Pack M8 @ $100)")

    so_item_name = so2.get("items")[0]["name"]

    # Round 1: Create invoice, submit, cancel
    inv1 = make_sales_invoice(so2.name)
    inv1.save()
    inv1.submit()
    billed_after_submit_1 = db.get_value("Sales Order Item", so_item_name, "billed_qty")
    print(f"  Invoice 1 ({inv1.name}) submitted  -> SO billed_qty = {billed_after_submit_1}")
    assert flt(billed_after_submit_1) == 10, f"Expected billed_qty=10, got {billed_after_submit_1}"

    inv1.cancel()
    billed_after_cancel_1 = db.get_value("Sales Order Item", so_item_name, "billed_qty")
    print(f"  Invoice 1 ({inv1.name}) cancelled  -> SO billed_qty = {billed_after_cancel_1}")
    assert flt(billed_after_cancel_1) == 0, f"Expected billed_qty=0, got {billed_after_cancel_1}"

    # Round 2: Create another invoice, submit, cancel
    inv2 = make_sales_invoice(so2.name)
    inv2.save()
    inv2.submit()
    billed_after_submit_2 = db.get_value("Sales Order Item", so_item_name, "billed_qty")
    print(f"  Invoice 2 ({inv2.name}) submitted  -> SO billed_qty = {billed_after_submit_2}")
    assert flt(billed_after_submit_2) == 10, f"Expected billed_qty=10, got {billed_after_submit_2}"

    inv2.cancel()
    billed_after_cancel_2 = db.get_value("Sales Order Item", so_item_name, "billed_qty")
    print(f"  Invoice 2 ({inv2.name}) cancelled  -> SO billed_qty = {billed_after_cancel_2}")
    assert flt(billed_after_cancel_2) == 0, f"Expected billed_qty=0, got {billed_after_cancel_2}"

    # Round 3: Create final invoice, submit, leave it
    inv3 = make_sales_invoice(so2.name)
    inv3.save()
    inv3.submit()
    billed_after_submit_3 = db.get_value("Sales Order Item", so_item_name, "billed_qty")
    print(f"  Invoice 3 ({inv3.name}) submitted  -> SO billed_qty = {billed_after_submit_3}")
    assert flt(billed_after_submit_3) == 10, f"Expected billed_qty=10, got {billed_after_submit_3}"

    # Verify the SO per_billed is correct
    so2.reload()
    print(f"  Final SO per_billed: {so2.per_billed}%")
    assert flt(so2.per_billed) == 100, f"Expected per_billed=100, got {so2.per_billed}"

    print(f"\n  PASSED - billed_qty stays correct through 3 create/cancel cycles!")

    # =====================================================================
    # REGRESSION: Submit atomicity (failed on_submit rolls back docstatus)
    # =====================================================================
    print_header("10. REGRESSION - Failed submit rolls back docstatus")

    # Create an invoice with no company — on_submit will fail when
    # trying to create GL entries because accounts can't be resolved
    bad_invoice = SalesInvoice(
        customer="CUST-001",
        posting_date=nowdate(),
        # Deliberately omit company — GL posting will fail
        items=[
            _dict(item_code="ITEM-001", qty=1, rate=100),
        ],
    )
    bad_invoice.save()
    print(f"  Created {bad_invoice.name} with no company (will fail on submit)")

    try:
        bad_invoice.submit()
        print(f"  ERROR: submit should have failed!")
        assert False, "Submit should have raised an exception"
    except Exception as e:
        print(f"  Submit raised: {type(e).__name__}: {str(e)[:80]}")

    # Verify docstatus is still 0 (draft) — not stuck at 1
    actual_docstatus = db.get_value("Sales Invoice", bad_invoice.name, "docstatus")
    print(f"  Docstatus in DB after failed submit: {actual_docstatus}")
    assert actual_docstatus == 0, f"Expected docstatus=0 (rolled back), got {actual_docstatus}"
    print(f"  In-memory docstatus: {bad_invoice.docstatus}")
    assert bad_invoice.docstatus == 0, f"Expected in-memory docstatus=0, got {bad_invoice.docstatus}"

    print(f"\n  PASSED - Failed submit correctly rolled back docstatus!")

    # =====================================================================
    # RETURNS: Credit Note (Sales Invoice Return)
    # =====================================================================
    print_header("11. CREDIT NOTE - Return a Sales Invoice")

    from lambda_erp.accounting.sales_invoice import make_sales_return

    # Create and submit a fresh Sales Invoice for return testing
    sinv_for_return = SalesInvoice(
        customer="CUST-001",
        company="Lambda Corp",
        posting_date=nowdate(),
        items=[
            _dict(item_code="ITEM-001", qty=10, rate=100),
            _dict(item_code="ITEM-002", qty=5, rate=250),
        ],
        taxes=[
            _dict(charge_type="On Net Total", account_head="Tax Payable - LAMB",
                  description="Sales Tax 10%", rate=10, idx=1),
        ],
    )
    sinv_for_return.save()
    sinv_for_return.submit()
    original_outstanding = flt(sinv_for_return.outstanding_amount)
    print(f"  Original Invoice: {sinv_for_return.name}")
    print(f"  Grand Total: {fmt_money(sinv_for_return.grand_total, currency='USD')}")
    print(f"  Outstanding: {fmt_money(original_outstanding, currency='USD')}")

    # Create the return (credit note)
    credit_note = make_sales_return(sinv_for_return.name)
    credit_note.save()
    print(f"\n  Credit Note: {credit_note.name} (is_return={credit_note.is_return})")
    print(f"  Return Against: {credit_note.return_against}")
    print(f"  Grand Total: {fmt_money(credit_note.grand_total, currency='USD')} (negative = reversal)")

    # Verify negative quantities
    for item in credit_note.get("items"):
        assert flt(item["qty"]) < 0, f"Expected negative qty, got {item['qty']}"
    print(f"  Items have negative quantities: OK")

    # Submit the credit note
    credit_note.submit()
    print(f"\n  Credit Note submitted - GL entries posted:")
    print_gl_entries(db, credit_note.name)

    # Verify original invoice outstanding is reduced
    updated_outstanding = flt(db.get_value("Sales Invoice", sinv_for_return.name, "outstanding_amount"))
    print(f"\n  Original invoice outstanding after return: {fmt_money(updated_outstanding, currency='USD')}")
    assert updated_outstanding == 0, f"Expected outstanding=0, got {updated_outstanding}"

    # Cancel the credit note and verify outstanding is restored
    credit_note.cancel()
    restored_outstanding = flt(db.get_value("Sales Invoice", sinv_for_return.name, "outstanding_amount"))
    print(f"  After cancelling credit note, outstanding restored: {fmt_money(restored_outstanding, currency='USD')}")
    assert abs(restored_outstanding - original_outstanding) < 0.01, \
        f"Expected outstanding={original_outstanding}, got {restored_outstanding}"

    print(f"\n  PASSED - Credit Note flow verified!")

    # =====================================================================
    # RETURNS: Debit Note (Purchase Invoice Return)
    # =====================================================================
    print_header("12. DEBIT NOTE - Return a Purchase Invoice")

    from lambda_erp.accounting.purchase_invoice import make_purchase_return

    # Create and submit a fresh Purchase Invoice for return testing
    pinv_for_return = PurchaseInvoice(
        supplier="SUPP-001",
        company="Lambda Corp",
        posting_date=nowdate(),
        items=[
            _dict(item_code="ITEM-001", qty=20, rate=60),
        ],
    )
    pinv_for_return.save()
    pinv_for_return.submit()
    pinv_original_outstanding = flt(pinv_for_return.outstanding_amount)
    print(f"  Original Invoice: {pinv_for_return.name}")
    print(f"  Grand Total: {fmt_money(pinv_for_return.grand_total, currency='USD')}")
    print(f"  Outstanding: {fmt_money(pinv_original_outstanding, currency='USD')}")

    # Create the return (debit note)
    debit_note = make_purchase_return(pinv_for_return.name)
    debit_note.save()
    debit_note.submit()
    print(f"\n  Debit Note: {debit_note.name} (is_return={debit_note.is_return})")
    print(f"  Grand Total: {fmt_money(debit_note.grand_total, currency='USD')} (negative = reversal)")
    print(f"\n  GL Entries posted:")
    print_gl_entries(db, debit_note.name)

    # Verify original outstanding reduced
    pinv_updated = flt(db.get_value("Purchase Invoice", pinv_for_return.name, "outstanding_amount"))
    print(f"\n  Original invoice outstanding after return: {fmt_money(pinv_updated, currency='USD')}")
    assert pinv_updated == 0, f"Expected outstanding=0, got {pinv_updated}"

    print(f"\n  PASSED - Debit Note flow verified!")

    # =====================================================================
    # RETURNS: Delivery Note Return (stock back in)
    # =====================================================================
    print_header("13. DELIVERY NOTE RETURN - Stock comes back in")

    from lambda_erp.stock.delivery_note import DeliveryNote, make_delivery_return

    # Check stock before
    stock_before = get_stock_balance("ITEM-001", "Main Warehouse - LAMB")
    print(f"  Stock before DN: {stock_before.actual_qty} units of ITEM-001")

    # Create and submit a Delivery Note
    dn_for_return = DeliveryNote(
        customer="CUST-001",
        company="Lambda Corp",
        posting_date=nowdate(),
        items=[
            _dict(item_code="ITEM-001", qty=20, rate=60, warehouse="Main Warehouse - LAMB"),
        ],
    )
    dn_for_return.save()
    dn_for_return.submit()

    stock_after_dn = get_stock_balance("ITEM-001", "Main Warehouse - LAMB")
    print(f"  Stock after DN ({dn_for_return.name}): {stock_after_dn.actual_qty} units (shipped 20)")

    # Regression: Delivery Note MUST post GL entries (Dr COGS / Cr Stock In Hand).
    # Previously skipped silently because Company.stock_in_hand_account didn't exist
    # and SQLite's quoted-identifier quirk returned None instead of erroring.
    dn_gl = db.get_all("GL Entry", filters={"voucher_no": dn_for_return.name},
                      fields=["account", "debit", "credit"])
    assert dn_gl, f"DN {dn_for_return.name} posted no GL entries — stock_in_hand_account likely missing"

    # COGS must post at moving-average COST, not at the sell rate on the DN line.
    # Previously both sides used item.rate × qty which overstated COGS.
    dn_cogs = sum(flt(r["debit"]) for r in dn_gl if "Cost of Goods Sold" in (r["account"] or ""))
    sle_cost = abs(flt(db.sql(
        'SELECT COALESCE(SUM(stock_value_difference), 0) as c FROM "Stock Ledger Entry" '
        'WHERE voucher_no = ? AND is_cancelled = 0', [dn_for_return.name])[0]["c"]))
    assert dn_cogs > 0, "DN COGS not posted"
    assert abs(dn_cogs - sle_cost) < 0.01, \
        f"DN COGS ({dn_cogs}) should equal SLE stock_value_difference ({sle_cost}), not the sell rate"
    # Sell value would have been 20 × 60 = 1200. Cost basis here is the moving
    # average, which is != 60 because the item was received at other rates
    # earlier in the test. The fact that cogs != 1200 is the actual fix.
    print(f"  DN COGS at cost: {dn_cogs:.2f} (sell value would have been 1200.00)")

    # Create the return
    dn_return = make_delivery_return(dn_for_return.name)
    dn_return.save()
    dn_return.submit()

    stock_after_return = get_stock_balance("ITEM-001", "Main Warehouse - LAMB")
    print(f"  Stock after return ({dn_return.name}): {stock_after_return.actual_qty} units (returned 20)")
    assert stock_after_return.actual_qty == stock_before.actual_qty, \
        f"Expected stock={stock_before.actual_qty}, got {stock_after_return.actual_qty}"

    print(f"\n  PASSED - Delivery Note return correctly restored stock!")

    # =====================================================================
    # SALES INVOICE with update_stock=1 (direct ship)
    # Invoice should both reduce stock and post Dr COGS / Cr Stock In Hand.
    # =====================================================================
    print_header("14. SALES INVOICE - Direct ship (update_stock=1)")

    from lambda_erp.accounting.sales_invoice import SalesInvoice

    stock_before_direct = get_stock_balance("ITEM-001", "Main Warehouse - LAMB")
    si_direct = SalesInvoice(
        customer="CUST-001",
        company="Lambda Corp",
        posting_date=nowdate(),
        update_stock=1,
        items=[
            _dict(item_code="ITEM-001", qty=5, rate=80, warehouse="Main Warehouse - LAMB"),
        ],
    )
    si_direct.save()
    si_direct.submit()

    stock_after_direct = get_stock_balance("ITEM-001", "Main Warehouse - LAMB")
    print(f"  Stock before: {stock_before_direct.actual_qty}, after: {stock_after_direct.actual_qty}")
    assert flt(stock_after_direct.actual_qty) == flt(stock_before_direct.actual_qty) - 5, \
        "SalesInvoice.update_stock did not decrement stock"

    si_gl = db.get_all("GL Entry", filters={"voucher_no": si_direct.name},
                      fields=["account", "debit", "credit"])
    stock_in_hand_credit = sum(flt(r["credit"]) for r in si_gl
                               if "Stock In Hand" in (r["account"] or ""))
    # Must post at cost, not at the 80 USD sell rate.
    si_sle_cost = abs(flt(db.sql(
        'SELECT COALESCE(SUM(stock_value_difference), 0) as c FROM "Stock Ledger Entry" '
        'WHERE voucher_no = ? AND is_cancelled = 0', [si_direct.name])[0]["c"]))
    assert stock_in_hand_credit > 0, "SI update_stock did not credit Stock In Hand"
    assert abs(stock_in_hand_credit - si_sle_cost) < 0.01, \
        f"SI Stock In Hand credit ({stock_in_hand_credit}) should equal cost ({si_sle_cost}), not sell rate"
    print(f"  PASSED - Direct-ship SI decremented stock AND posted Dr COGS / Cr Stock In Hand @ cost {si_sle_cost:.2f}")

    # =====================================================================
    # COST-NOT-SELL guard: receive at one cost, sell at a different price,
    # and make sure COGS posts at cost — not at the sell rate on the line.
    # Previously both DN and SI-update_stock used item.rate as outgoing_rate.
    # =====================================================================
    print_header("15. COGS posted at COST not at SELL rate")

    from lambda_erp.stock.stock_entry import StockEntry

    # Fresh item so moving-average starts clean.
    db.insert("Item", _dict(
        name="ITEM-COST-TEST",
        item_name="Cost Test Widget",
        stock_uom="Nos",
        standard_rate=50,
        is_stock_item=1,
    ))

    # Receive 10 units at cost 50.
    se_receive = StockEntry(
        stock_entry_type="Material Receipt",
        company="Lambda Corp",
        posting_date=nowdate(),
        items=[
            _dict(item_code="ITEM-COST-TEST", qty=10, basic_rate=50,
                  t_warehouse="Main Warehouse - LAMB"),
        ],
    )
    se_receive.save()
    se_receive.submit()

    # Ship 4 units via SI-update_stock at a sell price of 125.
    si_cost_test = SalesInvoice(
        customer="CUST-001",
        company="Lambda Corp",
        posting_date=nowdate(),
        update_stock=1,
        items=[
            _dict(item_code="ITEM-COST-TEST", qty=4, rate=125,
                  warehouse="Main Warehouse - LAMB"),
        ],
    )
    si_cost_test.save()
    si_cost_test.submit()

    cost_test_gl = db.get_all("GL Entry",
                              filters={"voucher_no": si_cost_test.name},
                              fields=["account", "debit", "credit"])
    cogs_debit = sum(flt(r["debit"]) for r in cost_test_gl
                     if "Cost of Goods Sold" in (r["account"] or ""))
    expected_cost = flt(4 * 50, 2)   # 4 units at cost 50
    forbidden_sell = flt(4 * 125, 2)  # 4 units at sell 125

    print(f"  COGS posted: {cogs_debit:.2f}  (expected cost {expected_cost:.2f}, "
          f"NOT sell value {forbidden_sell:.2f})")
    assert abs(cogs_debit - expected_cost) < 0.01, \
        f"COGS debit {cogs_debit} must equal cost {expected_cost}, not sell {forbidden_sell}"
    assert abs(cogs_debit - forbidden_sell) > 0.01, \
        "Regression: COGS matched sell value, fix reverted"

    print(f"\n  PASSED - COGS posts at moving-average cost, not at invoice sell rate.")

    # =====================================================================
    # PURCHASE INVOICE with update_stock=1 (receive-and-bill in one step)
    # Posts Dr Stock In Hand / Cr AP directly — no prior Purchase Receipt.
    # Cancelling must reverse both the SLE and the GL.
    # =====================================================================
    print_header("16. PURCHASE INVOICE - Direct receive (update_stock=1)")

    from lambda_erp.accounting.purchase_invoice import PurchaseInvoice

    bin_before_pi = get_stock_balance("ITEM-COST-TEST", "Main Warehouse - LAMB")
    ap_balance_before = get_gl_balance("Accounts Payable - LAMB")
    sih_balance_before = get_gl_balance("Stock In Hand - LAMB")

    pi_direct = PurchaseInvoice(
        supplier="SUPP-005",
        company="Lambda Corp",
        posting_date=nowdate(),
        update_stock=1,
        items=[
            _dict(item_code="ITEM-COST-TEST", qty=8, rate=55,
                  warehouse="Main Warehouse - LAMB"),
        ],
    )
    pi_direct.save()
    pi_direct.submit()

    bin_after_pi = get_stock_balance("ITEM-COST-TEST", "Main Warehouse - LAMB")
    assert flt(bin_after_pi.actual_qty) == flt(bin_before_pi.actual_qty) + 8, \
        "PI.update_stock did not increase Bin quantity"
    print(f"  Stock before: {bin_before_pi.actual_qty}, after: {bin_after_pi.actual_qty} (received 8)")

    pi_gl = db.get_all("GL Entry", filters={"voucher_no": pi_direct.name},
                       fields=["account", "debit", "credit"])
    sih_debit = sum(flt(r["debit"]) for r in pi_gl
                    if "Stock In Hand" in (r["account"] or ""))
    srbnb_debit = sum(flt(r["debit"]) for r in pi_gl
                      if "Stock Received But Not Billed" in (r["account"] or ""))
    ap_credit = sum(flt(r["credit"]) for r in pi_gl
                    if "Accounts Payable" in (r["account"] or ""))
    expected_line = flt(8 * 55, 2)

    assert abs(sih_debit - expected_line) < 0.01, \
        f"PI.update_stock should Dr Stock In Hand {expected_line}, got {sih_debit}"
    assert srbnb_debit == 0, \
        f"PI.update_stock must NOT touch SRBNB (got {srbnb_debit}); that's the PR->PI path"
    assert ap_credit >= expected_line - 0.01, \
        f"AP credit {ap_credit} should be >= line total {expected_line}"
    print(f"  GL: Dr Stock In Hand {sih_debit:.2f} / Cr AP {ap_credit:.2f} — no SRBNB touched")

    # Cancel and verify both SLE and GL were reversed.
    pi_direct.cancel()
    bin_after_cancel = get_stock_balance("ITEM-COST-TEST", "Main Warehouse - LAMB")
    assert flt(bin_after_cancel.actual_qty) == flt(bin_before_pi.actual_qty), \
        "Cancelling PI.update_stock must restore Bin quantity"
    sih_balance_after_cancel = get_gl_balance("Stock In Hand - LAMB")
    ap_balance_after_cancel = get_gl_balance("Accounts Payable - LAMB")
    assert abs(sih_balance_after_cancel - sih_balance_before) < 0.01, \
        "Cancelling PI.update_stock must restore Stock In Hand balance"
    assert abs(ap_balance_after_cancel - ap_balance_before) < 0.01, \
        "Cancelling PI.update_stock must restore AP balance"
    print(f"  After cancel: Bin back to {bin_after_cancel.actual_qty}; SIH and AP balances restored")

    print(f"\n  PASSED - PI.update_stock receives goods, books Dr SIH / Cr AP, and cancels cleanly.")

    # Validation: update_stock=1 without a warehouse on a stock line must raise.
    try:
        pi_missing_wh = PurchaseInvoice(
            supplier="SUPP-005",
            company="Lambda Corp",
            posting_date=nowdate(),
            update_stock=1,
            items=[
                _dict(item_code="ITEM-COST-TEST", qty=1, rate=55),  # no warehouse
            ],
        )
        pi_missing_wh.save()
        raise AssertionError("PI with update_stock=1 and no warehouse should have raised")
    except Exception as err:
        assert "warehouse" in str(err).lower(), \
            f"Expected a warehouse validation error, got: {err}"
        print(f"  Validation: update_stock=1 without warehouse correctly rejected.")

    # =====================================================================
    # POS INVOICE with update_stock=1 must post Dr COGS / Cr SIH at cost.
    # Previously stock left the warehouse but no GL entry fired, so the
    # balance sheet overstated inventory forever.
    # =====================================================================
    print_header("17. POS INVOICE - update_stock posts COGS at cost")

    from lambda_erp.accounting.pos_invoice import POSInvoice

    # Receive ITEM-COST-TEST again at $50 so moving-average stays known.
    # (Previous test steps already brought it to $50 avg; one more receipt
    # keeps it there and ensures enough stock.)
    se_top_up = StockEntry(
        stock_entry_type="Material Receipt",
        company="Lambda Corp",
        posting_date=nowdate(),
        items=[
            _dict(item_code="ITEM-COST-TEST", qty=20, basic_rate=50,
                  t_warehouse="Main Warehouse - LAMB"),
        ],
    )
    se_top_up.save()
    se_top_up.submit()

    pos_bin_before = get_stock_balance("ITEM-COST-TEST", "Main Warehouse - LAMB")
    pos = POSInvoice(
        customer="CUST-001",
        company="Lambda Corp",
        posting_date=nowdate(),
        update_stock=1,
        items=[
            _dict(item_code="ITEM-COST-TEST", qty=3, rate=150,
                  warehouse="Main Warehouse - LAMB"),
        ],
        payments=[
            _dict(mode_of_payment="Cash", amount=450),
        ],
    )
    pos.save()
    pos.submit()

    pos_bin_after = get_stock_balance("ITEM-COST-TEST", "Main Warehouse - LAMB")
    assert flt(pos_bin_after.actual_qty) == flt(pos_bin_before.actual_qty) - 3, \
        "POS update_stock must decrement Bin"

    pos_gl = db.get_all("GL Entry", filters={"voucher_no": pos.name},
                        fields=["account", "debit", "credit"])
    pos_cogs = sum(flt(r["debit"]) for r in pos_gl
                   if "Cost of Goods Sold" in (r["account"] or ""))
    pos_sih_credit = sum(flt(r["credit"]) for r in pos_gl
                         if "Stock In Hand" in (r["account"] or ""))
    assert pos_cogs > 0, "POS update_stock did NOT post COGS — balance sheet will drift"
    assert abs(pos_cogs - flt(3 * 50, 2)) < 0.01, \
        f"POS COGS should be 3 * 50 = 150 (cost), got {pos_cogs}"
    assert abs(pos_cogs - flt(3 * 150, 2)) > 0.01, \
        "Regression: POS COGS matched sell value, cost-basis fix reverted"
    assert abs(pos_sih_credit - pos_cogs) < 0.01, \
        "POS Stock In Hand credit must equal COGS debit"
    print(f"  POS COGS: {pos_cogs:.2f} at cost (sell would be 450.00), Bin "
          f"{pos_bin_before.actual_qty} -> {pos_bin_after.actual_qty}")

    # =====================================================================
    # Stock Entry Material Receipt must credit Stock Adjustment, not SRBNB
    # (SRBNB is the supplier-payable clearing account). Opening-balance
    # imports use this path and were creating phantom supplier payables.
    # =====================================================================
    print_header("18. Stock Entry Material Receipt - correct contra account")

    se_manual = StockEntry(
        stock_entry_type="Material Receipt",
        company="Lambda Corp",
        posting_date=nowdate(),
        items=[
            _dict(item_code="ITEM-COST-TEST", qty=4, basic_rate=50,
                  t_warehouse="Main Warehouse - LAMB"),
        ],
    )
    se_manual.save()
    se_manual.submit()

    se_gl = db.get_all("GL Entry", filters={"voucher_no": se_manual.name},
                       fields=["account", "debit", "credit"])
    srbnb_touched = any("Stock Received But Not Billed" in (r["account"] or "") for r in se_gl)
    adj_credit = sum(flt(r["credit"]) for r in se_gl
                     if "Stock Adjustment" in (r["account"] or ""))
    assert not srbnb_touched, \
        "Regression: Material Receipt hit SRBNB again — creates a phantom supplier payable"
    assert adj_credit > 0, \
        "Material Receipt should credit Stock Adjustment (its correct contra)"
    print(f"  PASSED - Material Receipt now credits Stock Adjustment ({adj_credit:.2f}), not SRBNB")

    # =====================================================================
    # Opening Stock (the new dedicated stock_entry_type for initial seeding)
    # must credit Opening Balance Equity, not Stock Adjustment. Otherwise
    # day-one inventory distorts the P&L as a phantom "gain".
    # =====================================================================
    print_header("18b. Opening Stock - credits Opening Balance Equity")

    se_opening = StockEntry(
        stock_entry_type="Opening Stock",
        company="Lambda Corp",
        posting_date=nowdate(),
        items=[
            _dict(item_code="ITEM-COST-TEST", qty=2, basic_rate=50,
                  t_warehouse="Main Warehouse - LAMB"),
        ],
    )
    se_opening.save()
    se_opening.submit()

    opening_gl = db.get_all("GL Entry", filters={"voucher_no": se_opening.name},
                            fields=["account", "debit", "credit"])
    equity_credit = sum(flt(r["credit"]) for r in opening_gl
                        if "Opening Balance Equity" in (r["account"] or ""))
    adj_touched = any("Stock Adjustment" in (r["account"] or "") for r in opening_gl)
    assert equity_credit > 0, \
        "Opening Stock should credit Opening Balance Equity"
    assert not adj_touched, \
        "Regression: Opening Stock hit Stock Adjustment — P&L distortion is back"
    print(f"  PASSED - Opening Stock credits Opening Balance Equity ({equity_credit:.2f}), "
          f"Stock Adjustment untouched")

    # =====================================================================
    # make_sales_return against a direct-ship SI must also restore stock.
    # =====================================================================
    print_header("19. Sales return on direct-ship SI restores stock")

    from lambda_erp.accounting.sales_invoice import make_sales_return

    direct_si = SalesInvoice(
        customer="CUST-001",
        company="Lambda Corp",
        posting_date=nowdate(),
        update_stock=1,
        items=[
            _dict(item_code="ITEM-COST-TEST", qty=2, rate=150,
                  warehouse="Main Warehouse - LAMB"),
        ],
    )
    direct_si.save()
    direct_si.submit()

    bin_after_direct_si = get_stock_balance("ITEM-COST-TEST", "Main Warehouse - LAMB")

    si_return = make_sales_return(direct_si.name)
    si_return.save()
    si_return.submit()
    assert flt(si_return.get("update_stock")) == 1, \
        "make_sales_return did not carry update_stock from the original"

    bin_after_return = get_stock_balance("ITEM-COST-TEST", "Main Warehouse - LAMB")
    assert flt(bin_after_return.actual_qty) == flt(bin_after_direct_si.actual_qty) + 2, \
        "Sales return on a direct-ship SI must put stock back into the warehouse"
    print(f"  PASSED - Bin {bin_after_direct_si.actual_qty} -> "
          f"{bin_after_return.actual_qty} after return (stock restored)")

    # =====================================================================
    # make_purchase_return against a direct-receive PI must also remove stock.
    # =====================================================================
    print_header("20. Purchase return on direct-receive PI removes stock")

    from lambda_erp.accounting.purchase_invoice import make_purchase_return

    direct_pi = PurchaseInvoice(
        supplier="SUPP-005",
        company="Lambda Corp",
        posting_date=nowdate(),
        update_stock=1,
        items=[
            _dict(item_code="ITEM-COST-TEST", qty=6, rate=50,
                  warehouse="Main Warehouse - LAMB"),
        ],
    )
    direct_pi.save()
    direct_pi.submit()

    bin_after_direct_pi = get_stock_balance("ITEM-COST-TEST", "Main Warehouse - LAMB")

    pi_return = make_purchase_return(direct_pi.name)
    pi_return.save()
    pi_return.submit()
    assert flt(pi_return.get("update_stock")) == 1, \
        "make_purchase_return did not carry update_stock from the original"

    bin_after_pi_return = get_stock_balance("ITEM-COST-TEST", "Main Warehouse - LAMB")
    assert flt(bin_after_pi_return.actual_qty) == flt(bin_after_direct_pi.actual_qty) - 6, \
        "Purchase return on a direct-receive PI must remove the received stock"
    print(f"  PASSED - Bin {bin_after_direct_pi.actual_qty} -> "
          f"{bin_after_pi_return.actual_qty} after return (stock removed)")

    # =====================================================================
    # POS Invoice return: must carry update_stock so stock comes back in,
    # and refund payments are optional.
    # =====================================================================
    print_header("21. POS return restores stock and revenue")

    from lambda_erp.accounting.pos_invoice import POSInvoice, make_pos_return

    bin_before_pos_return = get_stock_balance("ITEM-COST-TEST", "Main Warehouse - LAMB")
    pos_for_return = POSInvoice(
        customer="CUST-001",
        company="Lambda Corp",
        posting_date=nowdate(),
        update_stock=1,
        items=[
            _dict(item_code="ITEM-COST-TEST", qty=4, rate=150,
                  warehouse="Main Warehouse - LAMB"),
        ],
        payments=[_dict(mode_of_payment="Cash", amount=600)],
    )
    pos_for_return.save()
    pos_for_return.submit()
    bin_after_pos_sale = get_stock_balance("ITEM-COST-TEST", "Main Warehouse - LAMB")

    pos_return = make_pos_return(pos_for_return.name)
    pos_return.save()
    pos_return.submit()  # no payments — refund is optional
    assert flt(pos_return.get("update_stock")) == 1, \
        "make_pos_return dropped update_stock"
    assert flt(pos_return.get("is_return")) == 1
    assert pos_return.return_against == pos_for_return.name

    bin_after_pos_return = get_stock_balance("ITEM-COST-TEST", "Main Warehouse - LAMB")
    assert flt(bin_after_pos_return.actual_qty) == flt(bin_before_pos_return.actual_qty), \
        "POS return must fully restore stock"

    pos_return_gl = db.get_all("GL Entry", filters={"voucher_no": pos_return.name},
                               fields=["account", "debit", "credit"])
    rev_debit = sum(flt(r["debit"]) for r in pos_return_gl
                    if "Sales Revenue" in (r["account"] or ""))
    assert rev_debit > 0, "POS return must debit Sales Revenue (reverse the sale)"
    print(f"  Bin {bin_before_pos_return.actual_qty} -> {bin_after_pos_sale.actual_qty} "
          f"(sold) -> {bin_after_pos_return.actual_qty} (returned), revenue reversed")

    # =====================================================================
    # Exclusivity: update_stock=1 on a Sales Invoice that references a
    # Sales Order with an existing Delivery Note must be rejected.
    # =====================================================================
    print_header("22. Exclusivity: SI update_stock blocked if DN exists for SO")

    from lambda_erp.selling.quotation import Quotation
    from lambda_erp.selling.quotation import make_sales_order
    from lambda_erp.stock.delivery_note import make_delivery_note

    qtn_excl = Quotation(
        customer="CUST-001",
        company="Lambda Corp",
        transaction_date=nowdate(),
        items=[
            _dict(item_code="ITEM-COST-TEST", qty=3, rate=100,
                  warehouse="Main Warehouse - LAMB"),
        ],
    )
    qtn_excl.save()
    qtn_excl.submit()
    so_excl = make_sales_order(qtn_excl.name)
    so_excl.save()
    so_excl.submit()
    dn_excl = make_delivery_note(so_excl.name)
    dn_excl.save()
    dn_excl.submit()

    # A Sales Invoice referencing the same SO with update_stock=1 should be
    # rejected before it ships stock a second time.
    try:
        si_excl = SalesInvoice(
            customer="CUST-001",
            company="Lambda Corp",
            posting_date=nowdate(),
            update_stock=1,
            sales_order=so_excl.name,
            items=[
                _dict(item_code="ITEM-COST-TEST", qty=3, rate=100,
                      warehouse="Main Warehouse - LAMB",
                      sales_order=so_excl.name,
                      sales_order_item=so_excl.get("items")[0]["name"]),
            ],
        )
        si_excl.save()
        raise AssertionError("Expected validation error for double-shipment")
    except Exception as err:
        assert "already shipped" in str(err).lower() or "already shipped" in str(err), \
            f"Expected double-shipment error, got: {err}"
        print(f"  Rejected with: {err}")

    # =====================================================================
    # Exclusivity: update_stock=1 on a PI that references a PO with a PR
    # must be rejected.
    # =====================================================================
    print_header("23. Exclusivity: PI update_stock blocked if PR exists for PO")

    from lambda_erp.buying.purchase_order import PurchaseOrder
    from lambda_erp.stock.purchase_receipt import make_purchase_receipt

    po_excl = PurchaseOrder(
        supplier="SUPP-005",
        company="Lambda Corp",
        transaction_date=nowdate(),
        items=[
            _dict(item_code="ITEM-COST-TEST", qty=5, rate=50,
                  warehouse="Main Warehouse - LAMB"),
        ],
    )
    po_excl.save()
    po_excl.submit()
    pr_excl = make_purchase_receipt(po_excl.name)
    pr_excl.save()
    pr_excl.submit()

    try:
        pi_excl = PurchaseInvoice(
            supplier="SUPP-005",
            company="Lambda Corp",
            posting_date=nowdate(),
            update_stock=1,
            purchase_order=po_excl.name,
            items=[
                _dict(item_code="ITEM-COST-TEST", qty=5, rate=50,
                      warehouse="Main Warehouse - LAMB",
                      purchase_order=po_excl.name,
                      purchase_order_item=po_excl.get("items")[0]["name"]),
            ],
        )
        pi_excl.save()
        raise AssertionError("Expected validation error for double-receipt")
    except Exception as err:
        assert "already received" in str(err).lower() or "already received" in str(err), \
            f"Expected double-receipt error, got: {err}"
        print(f"  Rejected with: {err}")

    # =====================================================================
    # Cancel chain: PR cannot be cancelled while PI is still submitted
    # (because the PI already cleared the SRBNB entry the PR created).
    # =====================================================================
    print_header("24. Cancel chain: PR blocked while PI submitted")

    po_chain = PurchaseOrder(
        supplier="SUPP-005",
        company="Lambda Corp",
        transaction_date=nowdate(),
        items=[
            _dict(item_code="ITEM-COST-TEST", qty=2, rate=50,
                  warehouse="Main Warehouse - LAMB"),
        ],
    )
    po_chain.save()
    po_chain.submit()
    pr_chain = make_purchase_receipt(po_chain.name)
    pr_chain.save()
    pr_chain.submit()
    pi_chain = make_purchase_invoice(po_chain.name)
    pi_chain.save()
    pi_chain.submit()

    try:
        pr_chain.cancel()
        raise AssertionError("PR.cancel should have been blocked")
    except Exception as err:
        assert "Purchase Invoice" in str(err), f"Unexpected error: {err}"
        print(f"  Blocked with: {err}")

    pi_chain.cancel()
    pr_chain.cancel()
    print("  PR.cancel succeeded after PI was cancelled first.")

    # =====================================================================
    # Cancel chain: SI cannot be cancelled while a Payment Entry references it
    # (otherwise AR swings negative and the cash in Bank loses its backing).
    # =====================================================================
    print_header("25. Cancel chain: SI blocked while Payment Entry allocated")

    from lambda_erp.accounting.payment_entry import PaymentEntry

    si_pe = SalesInvoice(
        customer="CUST-001",
        company="Lambda Corp",
        posting_date=nowdate(),
        items=[_dict(item_code="SVC-001", qty=1, rate=200)],
    )
    si_pe.save()
    si_pe.submit()

    pe = PaymentEntry(
        payment_type="Receive",
        party_type="Customer",
        party="CUST-001",
        company="Lambda Corp",
        posting_date=nowdate(),
        paid_from="Accounts Receivable - LAMB",
        paid_to="Primary Bank - LAMB",
        paid_amount=200,
        received_amount=200,
        references=[_dict(reference_doctype="Sales Invoice",
                          reference_name=si_pe.name,
                          allocated_amount=200)],
    )
    pe.save()
    pe.submit()

    try:
        si_pe.cancel()
        raise AssertionError("SI.cancel should have been blocked")
    except Exception as err:
        assert "Payment Entry" in str(err), f"Unexpected error: {err}"
        print(f"  Blocked with: {err}")

    pe.cancel()
    si_pe.cancel()
    print("  SI.cancel succeeded after PE was cancelled first.")

    # =====================================================================
    # Master-link validation: nonexistent customer/supplier/item references
    # must be rejected on save. Previously PurchaseInvoice.validate only
    # checked truthiness, so SUPP-XYZ went through to persistence.
    # =====================================================================
    print_header("26. Master-link validation rejects phantom references")

    # Nonexistent supplier
    try:
        bad_pi = PurchaseInvoice(
            supplier="SUPP-GHOST",
            company="Lambda Corp",
            posting_date=nowdate(),
            items=[_dict(item_code="ITEM-001", qty=1, rate=10)],
        )
        bad_pi.save()
        raise AssertionError("Save should have failed for nonexistent supplier")
    except Exception as err:
        assert "Supplier" in str(err) and "SUPP-GHOST" in str(err), \
            f"Unexpected error: {err}"
        print(f"  Supplier check: {err}")

    # Nonexistent item on a child row
    try:
        bad_si = SalesInvoice(
            customer="CUST-001",
            company="Lambda Corp",
            posting_date=nowdate(),
            items=[_dict(item_code="ITEM-NONE", qty=1, rate=10)],
        )
        bad_si.save()
        raise AssertionError("Save should have failed for nonexistent item")
    except Exception as err:
        assert "Item" in str(err) and "ITEM-NONE" in str(err), \
            f"Unexpected error: {err}"
        print(f"  Item check:     {err}")

    # Nonexistent warehouse on stock entry
    try:
        bad_se = StockEntry(
            stock_entry_type="Material Receipt",
            company="Lambda Corp",
            posting_date=nowdate(),
            to_warehouse="Ghost WH - LAMB",
            items=[_dict(item_code="ITEM-001", qty=1, basic_rate=10,
                         t_warehouse="Ghost WH - LAMB")],
        )
        bad_se.save()
        raise AssertionError("Save should have failed for nonexistent warehouse")
    except Exception as err:
        assert "Warehouse" in str(err) and "Ghost WH" in str(err), \
            f"Unexpected error: {err}"
        print(f"  Warehouse check:{err}")

    # Valid references still pass
    ok_pi = PurchaseInvoice(
        supplier="SUPP-001",
        company="Lambda Corp",
        posting_date=nowdate(),
        items=[_dict(item_code="ITEM-001", qty=1, rate=10)],
    )
    ok_pi.save()
    print(f"  Valid references:   saved {ok_pi.name} without complaint")

    # =====================================================================
    # PE: refund flows + hardened validation
    # - Customer refund via Pay+Customer against a return SI.
    # - PE rejects cross-party allocation.
    # - PE rejects over-allocation.
    # - PE rejects allocation against a cancelled invoice.
    # =====================================================================
    print_header("27. PE refund flow + validation guards")

    from lambda_erp.accounting.payment_entry import PaymentEntry
    from lambda_erp.accounting.sales_invoice import make_sales_return
    from lambda_erp.accounting.purchase_invoice import make_purchase_return

    # A regular SI → pay it → return it → refund the customer via Pay+Customer.
    refund_si = SalesInvoice(
        customer="CUST-001",
        company="Lambda Corp",
        posting_date=nowdate(),
        items=[_dict(item_code="SVC-001", qty=1, rate=300)],
    )
    refund_si.save(); refund_si.submit()

    pe_pay_in = PaymentEntry(
        payment_type="Receive",
        party_type="Customer",
        party="CUST-001",
        company="Lambda Corp",
        posting_date=nowdate(),
        paid_from="Accounts Receivable - LAMB",
        paid_to="Primary Bank - LAMB",
        paid_amount=300,
        received_amount=300,
        references=[_dict(reference_doctype="Sales Invoice",
                          reference_name=refund_si.name,
                          allocated_amount=300)],
    )
    pe_pay_in.save(); pe_pay_in.submit()

    ret_si = make_sales_return(refund_si.name)
    ret_si.save(); ret_si.submit()
    ret_loaded = SalesInvoice.load(ret_si.name)
    assert flt(ret_loaded.outstanding_amount) < 0, \
        "Return SI should have negative outstanding"

    # Customer refund: Pay + Customer against the return SI.
    refund_pe = PaymentEntry(
        payment_type="Pay",
        party_type="Customer",
        party="CUST-001",
        company="Lambda Corp",
        posting_date=nowdate(),
        paid_from="Primary Bank - LAMB",
        paid_to="Accounts Receivable - LAMB",
        paid_amount=300,
        received_amount=300,
        references=[_dict(reference_doctype="Sales Invoice",
                          reference_name=ret_si.name,
                          allocated_amount=300)],
    )
    refund_pe.save(); refund_pe.submit()

    ret_after = SalesInvoice.load(ret_si.name)
    assert abs(flt(ret_after.outstanding_amount)) < 0.01, \
        f"Return SI outstanding should be 0 after refund, got {ret_after.outstanding_amount}"
    print(f"  Customer refund via Pay+Customer: return SI outstanding = {ret_after.outstanding_amount}")

    refund_defaults = PaymentEntry(
        payment_type="Pay",
        party_type="Customer",
        party="CUST-001",
        company="Lambda Corp",
        posting_date=nowdate(),
        paid_amount=10,
        received_amount=10,
    )
    refund_defaults.save()
    assert refund_defaults.paid_from == "Primary Bank - LAMB"
    assert refund_defaults.paid_to == "Accounts Receivable - LAMB"
    print(f"  Refund defaults: Pay+Customer -> {refund_defaults.paid_from} / {refund_defaults.paid_to}")

    # Wrong-party allocation — PE party doesn't match invoice party.
    wrong_party_si = SalesInvoice(
        customer="CUST-002",
        company="Lambda Corp",
        posting_date=nowdate(),
        items=[_dict(item_code="SVC-001", qty=1, rate=100)],
    )
    wrong_party_si.save(); wrong_party_si.submit()
    try:
        PaymentEntry(
            payment_type="Receive",
            party_type="Customer",
            party="CUST-001",
            company="Lambda Corp",
            posting_date=nowdate(),
            paid_from="Accounts Receivable - LAMB",
            paid_to="Primary Bank - LAMB",
            paid_amount=100,
            received_amount=100,
            references=[_dict(reference_doctype="Sales Invoice",
                              reference_name=wrong_party_si.name,
                              allocated_amount=100)],
        ).save()
        raise AssertionError("Cross-party allocation should have raised")
    except Exception as err:
        assert "belongs to" in str(err), f"Unexpected: {err}"
        print(f"  Cross-party: {err}")

    # Over-allocation — more than the invoice's remaining outstanding.
    try:
        PaymentEntry(
            payment_type="Receive",
            party_type="Customer",
            party="CUST-002",
            company="Lambda Corp",
            posting_date=nowdate(),
            paid_from="Accounts Receivable - LAMB",
            paid_to="Primary Bank - LAMB",
            paid_amount=500,
            received_amount=500,
            references=[_dict(reference_doctype="Sales Invoice",
                              reference_name=wrong_party_si.name,
                              allocated_amount=500)],
        ).save()
        raise AssertionError("Over-allocation should have raised")
    except Exception as err:
        assert "exceeds" in str(err).lower(), f"Unexpected: {err}"
        print(f"  Over-allocation: {err}")

    # Cancelled-invoice guard: PE cannot allocate to a cancelled invoice.
    cancelled_si = SalesInvoice(
        customer="CUST-002",
        company="Lambda Corp",
        posting_date=nowdate(),
        items=[_dict(item_code="SVC-001", qty=1, rate=100)],
    )
    cancelled_si.save(); cancelled_si.submit()
    cancelled_si.cancel()
    try:
        PaymentEntry(
            payment_type="Receive",
            party_type="Customer",
            party="CUST-002",
            company="Lambda Corp",
            posting_date=nowdate(),
            paid_from="Accounts Receivable - LAMB",
            paid_to="Primary Bank - LAMB",
            paid_amount=100,
            received_amount=100,
            references=[_dict(reference_doctype="Sales Invoice",
                              reference_name=cancelled_si.name,
                              allocated_amount=100)],
        ).save()
        raise AssertionError("Cancelled-invoice allocation should have raised")
    except Exception as err:
        assert "not submitted" in str(err), f"Unexpected: {err}"
        print(f"  Cancelled invoice: {err}")

    # Wrong direction: Customer PE allocating to a Purchase Invoice.
    any_pi = PurchaseInvoice(
        supplier="SUPP-001",
        company="Lambda Corp",
        posting_date=nowdate(),
        items=[_dict(item_code="SVC-001", qty=1, rate=10)],
    )
    any_pi.save(); any_pi.submit()
    try:
        PaymentEntry(
            payment_type="Pay",
            party_type="Customer",
            party="CUST-001",
            company="Lambda Corp",
            posting_date=nowdate(),
            paid_from="Primary Bank - LAMB",
            paid_to="Accounts Receivable - LAMB",
            paid_amount=10,
            received_amount=10,
            references=[_dict(reference_doctype="Purchase Invoice",
                              reference_name=any_pi.name,
                              allocated_amount=10)],
        ).save()
        raise AssertionError("Wrong-direction allocation should have raised")
    except Exception as err:
        assert "Cannot allocate" in str(err), f"Unexpected: {err}"
        print(f"  Wrong direction: {err}")

    # Supplier refund: Receive + Supplier against return PI.
    supplier_pi = PurchaseInvoice(
        supplier="SUPP-001",
        company="Lambda Corp",
        posting_date=nowdate(),
        items=[_dict(item_code="SVC-001", qty=1, rate=200)],
    )
    supplier_pi.save(); supplier_pi.submit()
    pay_supplier = PaymentEntry(
        payment_type="Pay",
        party_type="Supplier",
        party="SUPP-001",
        company="Lambda Corp",
        posting_date=nowdate(),
        paid_from="Primary Bank - LAMB",
        paid_to="Accounts Payable - LAMB",
        paid_amount=200,
        received_amount=200,
        references=[_dict(reference_doctype="Purchase Invoice",
                          reference_name=supplier_pi.name,
                          allocated_amount=200)],
    )
    pay_supplier.save(); pay_supplier.submit()
    ret_pi = make_purchase_return(supplier_pi.name)
    ret_pi.save(); ret_pi.submit()

    refund_from_supplier = PaymentEntry(
        payment_type="Receive",
        party_type="Supplier",
        party="SUPP-001",
        company="Lambda Corp",
        posting_date=nowdate(),
        paid_from="Accounts Payable - LAMB",
        paid_to="Primary Bank - LAMB",
        paid_amount=200,
        received_amount=200,
        references=[_dict(reference_doctype="Purchase Invoice",
                          reference_name=ret_pi.name,
                          allocated_amount=200)],
    )
    refund_from_supplier.save(); refund_from_supplier.submit()
    ret_pi_after = PurchaseInvoice.load(ret_pi.name)
    assert abs(flt(ret_pi_after.outstanding_amount)) < 0.01, \
        f"Return PI outstanding should be 0 after supplier refund, got {ret_pi_after.outstanding_amount}"
    print(f"  Supplier refund via Receive+Supplier: return PI outstanding = {ret_pi_after.outstanding_amount}")

    supplier_refund_defaults = PaymentEntry(
        payment_type="Receive",
        party_type="Supplier",
        party="SUPP-001",
        company="Lambda Corp",
        posting_date=nowdate(),
        paid_amount=10,
        received_amount=10,
    )
    supplier_refund_defaults.save()
    assert supplier_refund_defaults.paid_from == "Accounts Payable - LAMB"
    assert supplier_refund_defaults.paid_to == "Primary Bank - LAMB"
    print(
        "  Refund defaults: Receive+Supplier -> "
        f"{supplier_refund_defaults.paid_from} / {supplier_refund_defaults.paid_to}"
    )

    # =====================================================================
    # JE: line with reference_name updates invoice outstanding.
    # Write-off scenario: customer invoice for $100, we write off $40.
    # JE: Dr Bad Debts 40, Cr AR (ref=SI) 40.
    # Expected: SI outstanding drops by 40.
    # =====================================================================
    print_header("28. Journal Entry write-off syncs invoice outstanding")

    from lambda_erp.accounting.journal_entry import JournalEntry

    writeoff_si = SalesInvoice(
        customer="CUST-001",
        company="Lambda Corp",
        posting_date=nowdate(),
        items=[_dict(item_code="SVC-001", qty=1, rate=100)],
    )
    writeoff_si.save(); writeoff_si.submit()
    assert flt(writeoff_si.outstanding_amount) == 100

    je = JournalEntry(
        company="Lambda Corp",
        posting_date=nowdate(),
        accounts=[
            _dict(account="Administrative Expenses - LAMB", debit=40, credit=0),
            _dict(account="Accounts Receivable - LAMB", debit=0, credit=40,
                  party_type="Customer", party="CUST-001",
                  reference_doctype="Sales Invoice",
                  reference_name=writeoff_si.name),
        ],
    )
    je.save(); je.submit()

    wsi_after = SalesInvoice.load(writeoff_si.name)
    assert abs(flt(wsi_after.outstanding_amount) - 60) < 0.01, \
        f"SI outstanding should be 60 after $40 write-off, got {wsi_after.outstanding_amount}"
    print(f"  JE wrote off $40: SI outstanding 100 -> {wsi_after.outstanding_amount}")

    # Cancel JE: outstanding must restore.
    je.cancel()
    wsi_restored = SalesInvoice.load(writeoff_si.name)
    assert abs(flt(wsi_restored.outstanding_amount) - 100) < 0.01, \
        f"SI outstanding should be 100 after JE cancel, got {wsi_restored.outstanding_amount}"
    print(f"  JE cancelled: SI outstanding restored to {wsi_restored.outstanding_amount}")

    try:
        JournalEntry(
            company="Lambda Corp",
            posting_date=nowdate(),
            accounts=[
                _dict(account="Administrative Expenses - LAMB", debit=200, credit=0),
                _dict(account="Accounts Receivable - LAMB", debit=0, credit=200,
                      party_type="Customer", party="CUST-001",
                      reference_doctype="Sales Invoice",
                      reference_name=writeoff_si.name),
            ],
        ).save()
        raise AssertionError("JE over-reduction should have raised")
    except Exception as err:
        assert "exceeds its remaining outstanding" in str(err), f"Unexpected: {err}"
        print(f"  JE over-reduction: {err}")

    other_party_si = SalesInvoice(
        customer="CUST-002",
        company="Lambda Corp",
        posting_date=nowdate(),
        items=[_dict(item_code="SVC-001", qty=1, rate=50)],
    )
    other_party_si.save(); other_party_si.submit()
    try:
        JournalEntry(
            company="Lambda Corp",
            posting_date=nowdate(),
            accounts=[
                _dict(account="Administrative Expenses - LAMB", debit=10, credit=0),
                _dict(account="Accounts Receivable - LAMB", debit=0, credit=10,
                      party_type="Customer", party="CUST-001",
                      reference_doctype="Sales Invoice",
                      reference_name=other_party_si.name),
            ],
        ).save()
        raise AssertionError("JE cross-party reference should have raised")
    except Exception as err:
        assert "belongs to Customer" in str(err), f"Unexpected: {err}"
        print(f"  JE cross-party: {err}")

    # Rounding snap: 100 / 3 three times should clear exactly to 0.
    split_si = SalesInvoice(
        customer="CUST-001",
        company="Lambda Corp",
        posting_date=nowdate(),
        items=[_dict(item_code="SVC-001", qty=1, rate=100)],
    )
    split_si.save(); split_si.submit()
    for amt in (33.33, 33.33, 33.34):
        PaymentEntry(
            payment_type="Receive",
            party_type="Customer",
            party="CUST-001",
            company="Lambda Corp",
            posting_date=nowdate(),
            paid_from="Accounts Receivable - LAMB",
            paid_to="Primary Bank - LAMB",
            paid_amount=amt,
            received_amount=amt,
            references=[_dict(reference_doctype="Sales Invoice",
                              reference_name=split_si.name,
                              allocated_amount=amt)],
        ).save().submit()
    split_after = SalesInvoice.load(split_si.name)
    assert flt(split_after.outstanding_amount) == 0, \
        f"Split payment should snap to 0, got {split_after.outstanding_amount}"
    print(f"  100 split as 33.33+33.33+33.34: SI outstanding = {split_after.outstanding_amount}")

    # =====================================================================
    # Double-return guard: a second return that would over-return an
    # already-partially-returned invoice must be rejected.
    # =====================================================================
    print_header("29. Double-return blocked on SI and PI")

    original_si = SalesInvoice(
        customer="CUST-001",
        company="Lambda Corp",
        posting_date=nowdate(),
        items=[_dict(item_code="SVC-001", qty=2, rate=100)],
    )
    original_si.save(); original_si.submit()

    # First return: 1 of 2 units.
    first_ret = make_sales_return(original_si.name)
    first_ret.get("items")[0]["qty"] = -1
    first_ret.save(); first_ret.submit()
    print(f"  First return 1/2 accepted: {first_ret.name}")

    # Second return of 1 unit: allowed (1 remaining).
    second_ok = make_sales_return(original_si.name)
    second_ok.get("items")[0]["qty"] = -1
    second_ok.save(); second_ok.submit()
    print(f"  Second return 1/2 accepted: {second_ok.name}")

    # Third return: rejected (nothing left).
    try:
        third_bad = make_sales_return(original_si.name)
        third_bad.get("items")[0]["qty"] = -1
        third_bad.save()
        raise AssertionError("Third return should have been rejected")
    except Exception as err:
        assert "exceeds" in str(err).lower() and "remaining" in str(err).lower(), \
            f"Unexpected error: {err}"
        print(f"  Third return rejected: {err}")

    # Same on the Purchase side.
    original_pi = PurchaseInvoice(
        supplier="SUPP-001",
        company="Lambda Corp",
        posting_date=nowdate(),
        items=[_dict(item_code="SVC-001", qty=2, rate=50)],
    )
    original_pi.save(); original_pi.submit()
    first_pret = make_purchase_return(original_pi.name)
    first_pret.get("items")[0]["qty"] = -2
    first_pret.save(); first_pret.submit()
    try:
        second_pret_bad = make_purchase_return(original_pi.name)
        second_pret_bad.get("items")[0]["qty"] = -1
        second_pret_bad.save()
        raise AssertionError("Second PI return should have been rejected")
    except Exception as err:
        assert "exceeds" in str(err).lower() and "remaining" in str(err).lower(), \
            f"Unexpected error: {err}"
        print(f"  Second PI return rejected: {err}")

    # =====================================================================
    # Dynamic-link validation: `party` and `reference_name` resolve their
    # target doctype at runtime from a sibling type field. Typos must be
    # rejected on save.
    # =====================================================================
    print_header("30. Dynamic-link validation on PE / JE")

    try:
        PaymentEntry(
            payment_type="Receive",
            party_type="Customer",
            party="CUST-GHOST",
            company="Lambda Corp",
            posting_date=nowdate(),
            paid_from="Accounts Receivable - LAMB",
            paid_to="Primary Bank - LAMB",
            paid_amount=100,
            received_amount=100,
        ).save()
        raise AssertionError("PE with phantom party should have been rejected")
    except Exception as err:
        assert "CUST-GHOST" in str(err) and "Customer" in str(err), \
            f"Unexpected error: {err}"
        print(f"  PE phantom party: {err}")

    try:
        PaymentEntry(
            payment_type="Receive",
            party_type="Customer",
            party="CUST-001",
            company="Lambda Corp",
            posting_date=nowdate(),
            paid_from="Accounts Receivable - LAMB",
            paid_to="Primary Bank - LAMB",
            paid_amount=100,
            received_amount=100,
            references=[_dict(reference_doctype="Sales Invoice",
                              reference_name="SINV-NOPE",
                              allocated_amount=100)],
        ).save()
        raise AssertionError("PE with phantom reference_name should have been rejected")
    except Exception as err:
        assert "SINV-NOPE" in str(err), f"Unexpected error: {err}"
        print(f"  PE phantom reference: {err}")

    try:
        JournalEntry(
            company="Lambda Corp",
            posting_date=nowdate(),
            accounts=[
                _dict(account="Administrative Expenses - LAMB", debit=10, credit=0),
                _dict(account="Accounts Receivable - LAMB", debit=0, credit=10,
                      party_type="Customer", party="CUST-GHOST"),
            ],
        ).save()
        raise AssertionError("JE with phantom party should have been rejected")
    except Exception as err:
        assert "CUST-GHOST" in str(err), f"Unexpected error: {err}"
        print(f"  JE phantom party: {err}")

    try:
        PaymentEntry(
            payment_type="Receive",
            company="Lambda Corp",
            posting_date=nowdate(),
            paid_from="Accounts Receivable - LAMB",
            paid_to="Primary Bank - LAMB",
            paid_amount=100,
            received_amount=100,
        ).save()
        raise AssertionError("PE without party context should have been rejected")
    except Exception as err:
        assert "Party Type is required" in str(err) or "Party is required" in str(err), \
            f"Unexpected error: {err}"
        print(f"  PE missing party: {err}")

    try:
        PaymentEntry(
            payment_type="Receive",
            party_type="Custmer",
            party="CUST-001",
            company="Lambda Corp",
            posting_date=nowdate(),
            paid_from="Accounts Receivable - LAMB",
            paid_to="Primary Bank - LAMB",
            paid_amount=100,
            received_amount=100,
        ).save()
        raise AssertionError("PE invalid party_type should have been rejected")
    except Exception as err:
        assert "Party Type is required" in str(err) or "not valid" in str(err), \
            f"Unexpected error: {err}"
        print(f"  PE invalid party_type: {err}")

    try:
        JournalEntry(
            company="Lambda Corp",
            posting_date=nowdate(),
            accounts=[
                _dict(account="Administrative Expenses - LAMB", debit=10, credit=0),
                _dict(account="Accounts Receivable - LAMB", debit=0, credit=10,
                      party_type="Custmer", party="CUST-001"),
            ],
        ).save()
        raise AssertionError("JE invalid party_type should have been rejected")
    except Exception as err:
        assert "not valid" in str(err), f"Unexpected error: {err}"
        print(f"  JE invalid party_type: {err}")

    # Return value guard: quantity can be in-bounds while amount is inflated.
    overvalue_si = SalesInvoice(
        customer="CUST-001",
        company="Lambda Corp",
        posting_date=nowdate(),
        items=[_dict(item_code="SVC-001", qty=1, rate=100)],
    )
    overvalue_si.save(); overvalue_si.submit()
    try:
        bad_credit = make_sales_return(overvalue_si.name)
        bad_credit.get("items")[0]["qty"] = -1
        bad_credit.get("items")[0]["rate"] = 500
        bad_credit.save()
        raise AssertionError("Overvalued sales return should have been rejected")
    except Exception as err:
        assert "remaining returnable value" in str(err), f"Unexpected error: {err}"
        print(f"  SI overvalue return: {err}")

    overvalue_pi = PurchaseInvoice(
        supplier="SUPP-001",
        company="Lambda Corp",
        posting_date=nowdate(),
        items=[_dict(item_code="SVC-001", qty=1, rate=100)],
    )
    overvalue_pi.save(); overvalue_pi.submit()
    try:
        bad_debit = make_purchase_return(overvalue_pi.name)
        bad_debit.get("items")[0]["qty"] = -1
        bad_debit.get("items")[0]["rate"] = 500
        bad_debit.save()
        raise AssertionError("Overvalued purchase return should have been rejected")
    except Exception as err:
        assert "remaining returnable value" in str(err), f"Unexpected error: {err}"
        print(f"  PI overvalue return: {err}")

    # =====================================================================
    # Submitted documents are immutable. save() on a submitted SI would
    # silently rewrite totals while GL stays frozen at the original values,
    # producing subledger drift.
    # =====================================================================
    print_header("31. Submitted docs reject .save()")

    immutable_si = SalesInvoice(
        customer="CUST-001",
        company="Lambda Corp",
        posting_date=nowdate(),
        items=[_dict(item_code="SVC-001", qty=1, rate=100)],
    )
    immutable_si.save(); immutable_si.submit()
    immutable_si.get("items")[0]["rate"] = 99999
    try:
        immutable_si.save()
        raise AssertionError("Saving a submitted SI should have been blocked")
    except Exception as err:
        assert "submitted" in str(err).lower() or "immutable" in str(err).lower(), \
            f"Unexpected error: {err}"
        print(f"  Post-submit save blocked: {err}")

    # =====================================================================
    # Account-type direction constraints. Posting revenue to an expense
    # account keeps the Trial Balance balanced but produces a junk P&L.
    # =====================================================================
    print_header("32. Account-type direction constraints")

    try:
        SalesInvoice(
            customer="CUST-001",
            company="Lambda Corp",
            posting_date=nowdate(),
            debit_to="Cost of Goods Sold - LAMB",
            items=[_dict(item_code="SVC-001", qty=1, rate=100)],
        ).save()
        raise AssertionError("SI with non-Receivable debit_to should have been rejected")
    except Exception as err:
        assert "debit" in str(err).lower(), f"Unexpected: {err}"
        print(f"  SI debit_to=COGS: {err}")

    try:
        SalesInvoice(
            customer="CUST-001",
            company="Lambda Corp",
            posting_date=nowdate(),
            items=[_dict(item_code="SVC-001", qty=1, rate=100,
                         income_account="Administrative Expenses - LAMB")],
        ).save()
        raise AssertionError("SI item with Expense-root income_account should have been rejected")
    except Exception as err:
        assert "income" in str(err).lower(), f"Unexpected: {err}"
        print(f"  SI income_account=Expense: {err}")

    try:
        PurchaseInvoice(
            supplier="SUPP-001",
            company="Lambda Corp",
            posting_date=nowdate(),
            credit_to="Primary Bank - LAMB",
            items=[_dict(item_code="SVC-001", qty=1, rate=50)],
        ).save()
        raise AssertionError("PI with Bank credit_to should have been rejected")
    except Exception as err:
        assert "credit" in str(err).lower(), f"Unexpected: {err}"
        print(f"  PI credit_to=Bank: {err}")

    # =====================================================================
    # 33. Actual-charge (freight / shipping) survives recalculation
    # =====================================================================
    # Regression test for a bug where initialize_taxes() zeroed
    # `tax_amount` on every recalc, including for charge_type="Actual"
    # rows whose tax_amount IS the user input. That made freight,
    # shipping, and customs charges silently vanish when a draft was
    # saved or a submitted invoice got loaded and re-saved.
    print_header("33. Actual-charge (freight) survives initialize_taxes reset")

    from lambda_erp.accounting.sales_invoice import SalesInvoice

    # --- (a) Single Actual charge: total = items + charge --------------
    si_a = SalesInvoice(
        customer="CUST-001",
        company="Lambda Corp",
        posting_date=nowdate(),
        items=[_dict(item_code="ITEM-001", qty=10, rate=100)],
        taxes=[
            _dict(
                charge_type="Actual",
                account_head="Freight In - LAMB",
                description="Shipping",
                tax_amount=185,
                add_deduct_tax="Add",
                idx=1,
            ),
        ],
    )
    si_a.save()
    assert flt(si_a.grand_total) == 1185.0, \
        f"Actual charge grand_total expected 1185.00, got {si_a.grand_total}"
    assert flt(si_a.total_taxes_and_charges) == 185.0, \
        f"total_taxes_and_charges expected 185.00, got {si_a.total_taxes_and_charges}"
    assert flt(si_a.get("taxes")[0]["tax_amount"]) == 185.0, \
        f"Actual tax_amount should be preserved, got {si_a.get('taxes')[0]['tax_amount']}"
    print(f"  Actual charge (freight 185 on items 1000): grand_total = {si_a.grand_total}")

    # --- (b) Re-save must be idempotent: no wipe, no double-count ------
    si_a.save()
    assert flt(si_a.grand_total) == 1185.0, \
        f"Second save changed grand_total: got {si_a.grand_total} (was 1185.00)"
    assert flt(si_a.get("taxes")[0]["tax_amount"]) == 185.0, \
        f"Second save mutated Actual tax_amount: got {si_a.get('taxes')[0]['tax_amount']}"
    print(f"  Second save preserved charge: grand_total = {si_a.grand_total}")

    # --- (c) DB round-trip: load + re-save must keep the charge --------
    si_a.submit()
    reloaded = SalesInvoice.load(si_a.name)
    # A submit-then-cancel-then-save cycle hits the same reset path; run
    # calculate_taxes_and_totals explicitly on the reloaded doc to
    # confirm the persisted tax_amount survives a fresh compute.
    from lambda_erp.controllers.taxes_and_totals import calculate_taxes_and_totals
    calculate_taxes_and_totals(reloaded)
    assert flt(reloaded.grand_total) == 1185.0, \
        f"Reload + recalc changed grand_total: got {reloaded.grand_total}"
    assert flt(reloaded.get("taxes")[0]["tax_amount"]) == 185.0, \
        f"Reload + recalc wiped Actual tax_amount: got {reloaded.get('taxes')[0]['tax_amount']}"
    print(f"  Load + recalc preserved charge: grand_total = {reloaded.grand_total}")

    # --- (d) Stacked: freight (Actual) + VAT (On Previous Row Total) ---
    # Classic EU shape: VAT base includes freight. freight first (idx=1),
    # VAT second (idx=2) referencing row 1.
    si_b = SalesInvoice(
        customer="CUST-001",
        company="Lambda Corp",
        posting_date=nowdate(),
        items=[_dict(item_code="ITEM-001", qty=10, rate=100)],
        taxes=[
            _dict(
                charge_type="Actual",
                account_head="Freight In - LAMB",
                description="Shipping",
                tax_amount=100,
                add_deduct_tax="Add",
                idx=1,
            ),
            _dict(
                charge_type="On Previous Row Total",
                account_head="Tax Payable - LAMB",
                description="VAT 10% on total incl. freight",
                rate=10,
                row_id=1,
                add_deduct_tax="Add",
                idx=2,
            ),
        ],
    )
    si_b.save()
    # items 1000 + freight 100 = 1100. VAT 10% of 1100 = 110.
    # Grand total should be 1000 + 100 + 110 = 1210.
    assert flt(si_b.grand_total) == 1210.0, \
        f"Stacked charge+tax grand_total expected 1210.00, got {si_b.grand_total}"
    assert flt(si_b.get("taxes")[0]["tax_amount"]) == 100.0, \
        f"Stacked row[0] Actual expected 100.00, got {si_b.get('taxes')[0]['tax_amount']}"
    assert flt(si_b.get("taxes")[1]["tax_amount"]) == 110.0, \
        f"Stacked row[1] VAT expected 110.00, got {si_b.get('taxes')[1]['tax_amount']}"
    print(f"  Stacked Actual + OnPrevRowTotal: grand_total = {si_b.grand_total} (1000 + 100 freight + 110 VAT)")

    # ---------------------------------------------------------------------
    # The item code lives in the Item PK column `name`, but callers (LLM tools,
    # REST clients) naturally refer to it as `item_code`. The master API accepts
    # that alias so a custom code like "SVC-SPARK" is honored instead of being
    # silently dropped and replaced with an auto-generated ITEM-NNN.
    # ---------------------------------------------------------------------
    print_header("34. REGRESSION - create_master honors a custom item_code alias")

    from api.routers.masters import create_master_record, update_master_record
    from fastapi import HTTPException

    created = create_master_record("item", {"item_code": "SVC-SPARK", "item_name": "Spark", "is_stock_item": 0})
    assert created["name"] == "SVC-SPARK", \
        f"Custom item_code should become the code, got {created['name']!r}"
    assert created.get("item_code") == "SVC-SPARK", \
        f"Result should echo item_code, got {created.get('item_code')!r}"
    assert db.exists("Item", "SVC-SPARK"), "Item SVC-SPARK should exist in the DB"
    print(f"  item_code alias honored: code = {created['name']}, item_name = {created['item_name']}")

    # An explicit `name` still wins over the alias.
    explicit = create_master_record("item", {"name": "SVC-FLOW", "item_code": "IGNORED", "item_name": "Flow"})
    assert explicit["name"] == "SVC-FLOW", \
        f"Explicit name should win, got {explicit['name']!r}"
    print(f"  Explicit name wins over alias: code = {explicit['name']}")

    # Omitting the code still auto-generates the ITEM-NNN fallback.
    auto = create_master_record("item", {"item_name": "Auto Numbered"})
    assert auto["name"].startswith("ITEM-"), \
        f"Missing code should auto-generate ITEM-NNN, got {auto['name']!r}"
    print(f"  Auto-numbered fallback intact: code = {auto['name']}")

    # Renaming the code via update must be rejected, not silently crash.
    try:
        update_master_record("item", "SVC-SPARK", {"item_code": "SVC-RENAMED"})
        raise AssertionError("Renaming an item_code should have been rejected")
    except HTTPException as err:
        assert err.status_code == 422 and "identity" in str(err.detail), \
            f"Unexpected error: {err.detail}"
    print(f"  Code rename correctly rejected (422)")

    # A matching item_code on update is a harmless no-op.
    updated = update_master_record("item", "SVC-SPARK", {"item_code": "SVC-SPARK", "standard_rate": 310})
    assert flt(updated["standard_rate"]) == 310.0, \
        f"Matching item_code should be a no-op and let other fields update, got {updated['standard_rate']}"
    print(f"  Matching item_code no-op; other fields updated: standard_rate = {updated['standard_rate']}")

    # The chat layer must NOT flag item_code as an ignored/unknown field — it
    # was honored, so a misleading "ignored" warning would just confuse the LLM.
    from api.chat import _ignored_master_fields
    assert _ignored_master_fields("item", {"item_code": "SVC-NEXUS", "item_name": "Nexus"}) == [], \
        "item_code must be recognized as a valid alias, not reported as ignored"
    print(f"  Chat layer treats item_code as a valid field (no spurious warning)")

    # A company creates from company_name alone — its id defaults to the name
    # (mirrors /setup/company) instead of the old hard "Name is required" 422.
    co = create_master_record("company", {"company_name": "Draftwerk GmbH", "default_currency": "CHF"})
    assert co["name"] == "Draftwerk GmbH", \
        f"Company id should default to company_name, got {co['name']!r}"
    print(f"  Company id defaults to company_name: {co['name']}")

    # Account / Cost Center have no auto-id, so an explicit `name` stays required
    # — but the failure must be the clear 422, not a silent miscreate.
    for mtype, payload in (
        ("account", {"account_name": "Draft Account", "company": "Lambda Corp"}),
        ("cost-center", {"cost_center_name": "Draft CC", "company": "Lambda Corp"}),
    ):
        try:
            create_master_record(mtype, dict(payload))
            raise AssertionError(f"{mtype} without name should have been rejected")
        except HTTPException as err:
            assert err.status_code == 422 and "Name is required" in str(err.detail), \
                f"Unexpected error for {mtype}: {err.detail}"
    print(f"  Account / Cost Center still require an explicit name (clear 422)")

    # ---------------------------------------------------------------------
    # Multi-currency: documents carry a currency + conversion_rate. The GL's
    # debit/credit must be in the company's base currency (doc amount * rate)
    # while *_in_account_currency stays in document currency. Stock valuation
    # is likewise in base currency so Stock-In-Hand GL matches the ledger.
    # ---------------------------------------------------------------------
    print_header("35. MULTI-CURRENCY - base-currency GL + currency defaulting")

    from lambda_erp.selling.quotation import Quotation as _Quotation
    from lambda_erp.accounting.sales_invoice import SalesInvoice as _SI
    from lambda_erp.accounting.purchase_invoice import PurchaseInvoice as _PI

    base_ccy = db.get_value("Company", "Lambda Corp", "default_currency")
    fx_rate = 1.1

    # --- (a) Foreign-currency Sales Invoice posts base AR + income ----------
    si_fx = _SI(
        customer="CUST-001", company="Lambda Corp", posting_date=nowdate(),
        currency="EUR", conversion_rate=fx_rate,
        items=[_dict(item_code="ITEM-001", qty=1, rate=100)],
    )
    si_fx.save()
    si_fx.submit()
    assert flt(si_fx.grand_total) == 100.0, f"doc-currency grand_total should be 100, got {si_fx.grand_total}"
    gl = db.get_all("GL Entry", filters={"voucher_no": si_fx.name, "is_cancelled": 0}, fields=["*"])
    ar = next(e for e in gl if flt(e["debit"]) > 0)
    inc = next(e for e in gl if flt(e["credit"]) > 0)
    assert flt(ar["debit"]) == 110.0, f"AR base debit should be 100*1.1=110, got {ar['debit']}"
    assert flt(ar["debit_in_account_currency"]) == 100.0, \
        f"AR document-currency amount should stay 100, got {ar['debit_in_account_currency']}"
    assert flt(inc["credit"]) == 110.0, f"Income base credit should be 110, got {inc['credit']}"
    print(f"  EUR Sales Invoice @1.1: AR/income posted at base 110.00, doc-currency 100.00")

    # --- (b) Foreign-currency Purchase Invoice (service) posts base AP/expense
    pi_fx = _PI(
        supplier="SUPP-001", company="Lambda Corp", posting_date=nowdate(),
        currency="EUR", conversion_rate=fx_rate,
        items=[_dict(item_code="SVC-001", qty=1, rate=100)],
    )
    pi_fx.save()
    pi_fx.submit()
    gl = db.get_all("GL Entry", filters={"voucher_no": pi_fx.name, "is_cancelled": 0}, fields=["*"])
    ap = next(e for e in gl if flt(e["credit"]) > 0)
    exp = next(e for e in gl if flt(e["debit"]) > 0)
    assert flt(ap["credit"]) == 110.0, f"AP base credit should be 110, got {ap['credit']}"
    assert flt(ap["credit_in_account_currency"]) == 100.0, \
        f"AP document-currency amount should stay 100, got {ap['credit_in_account_currency']}"
    assert flt(exp["debit"]) == 110.0, f"Expense base debit should be 110, got {exp['debit']}"
    print(f"  EUR Purchase Invoice @1.1: AP/expense posted at base 110.00")

    # --- (c) Foreign-currency PI update_stock values inventory in base ------
    sih_acct = db.get_value("Company", "Lambda Corp", "stock_in_hand_account")
    pi_stk = _PI(
        supplier="SUPP-001", company="Lambda Corp", posting_date=nowdate(),
        currency="EUR", conversion_rate=fx_rate, update_stock=1,
        items=[_dict(item_code="ITEM-001", qty=2, rate=50, warehouse="Main Warehouse - LAMB")],
    )
    pi_stk.save()
    pi_stk.submit()
    sle_val = db.sql(
        'SELECT COALESCE(SUM(stock_value_difference), 0) AS d FROM "Stock Ledger Entry" '
        'WHERE voucher_no = ? AND is_cancelled = 0', [pi_stk.name],
    )[0]["d"]
    assert flt(sle_val, 2) == 110.0, f"Stock value should be 2*50*1.1=110 (base), got {sle_val}"
    gl = db.get_all("GL Entry", filters={"voucher_no": pi_stk.name, "is_cancelled": 0}, fields=["*"])
    sih_gl = next(e for e in gl if e["account"] == sih_acct and flt(e["debit"]) > 0)
    assert flt(sih_gl["debit"]) == 110.0, \
        f"Stock-In-Hand GL ({sih_gl['debit']}) must match base stock value (110)"
    print(f"  EUR stock receipt @1.1: inventory + Stock-In-Hand GL both at base 110.00")

    # --- (d) Currency defaulting + auto-filled rate from Currency Exchange ---
    db.insert("Currency Exchange", _dict(
        name="EUR-USD-2020-01-01", date="2020-01-01",
        from_currency="EUR", to_currency="USD", exchange_rate=1.15,
    ))
    db.set_value("Customer", "CUST-002", "default_currency", "EUR")
    q_def = _Quotation(
        customer="CUST-002", company="Lambda Corp", transaction_date=nowdate(),
        items=[_dict(item_code="ITEM-001", qty=1, rate=100)],
    )
    q_def.save()
    assert q_def.currency == "EUR", f"currency should default from the customer, got {q_def.currency!r}"
    assert flt(q_def.conversion_rate) == 1.15, \
        f"conversion_rate should be auto-looked-up from the table (1.15), got {q_def.conversion_rate}"
    print(f"  New quotation inherited customer currency + auto rate: {q_def.currency} @ {q_def.conversion_rate}")

    # A base-currency document forces conversion_rate to 1.0 regardless of input.
    si_base = _SI(
        customer="CUST-001", company="Lambda Corp", posting_date=nowdate(),
        currency=base_ccy, conversion_rate=5.0,
        items=[_dict(item_code="ITEM-001", qty=1, rate=100)],
    )
    si_base.save()
    assert flt(si_base.conversion_rate) == 1.0, \
        f"base-currency doc must force rate 1.0, got {si_base.conversion_rate}"
    print(f"  Base-currency ({base_ccy}) doc forced conversion_rate to 1.0")

    # --- (e) The chat create_document path carries currency + conversion_rate
    from api.services import create_document as _svc_create_document
    draft = _svc_create_document("sales-invoice", {
        "customer": "CUST-001", "company": "Lambda Corp", "posting_date": nowdate(),
        "currency": "EUR", "conversion_rate": 1.2,
        "items": [{"item_code": "ITEM-001", "qty": 1, "rate": 100}],
    })
    assert draft["currency"] == "EUR" and flt(draft["conversion_rate"]) == 1.2, \
        f"chat create_document should honor currency/rate, got {draft.get('currency')}/{draft.get('conversion_rate')}"
    print(f"  chat create_document honored {draft['currency']} @ {draft['conversion_rate']}")

    # ---------------------------------------------------------------------
    # Exchange-rate lookup carries forward the latest rate on/before a date.
    # Posting snapshots the rate onto the document, so editing the rate table
    # never changes an already-posted book — that snapshot, not table
    # immutability, is what guarantees historical reproducibility.
    # ---------------------------------------------------------------------
    print_header("36. EXCHANGE RATE - lookup, guard, snapshot immutability")

    from lambda_erp.controllers.currency import get_exchange_rate
    from lambda_erp.exceptions import ValidationError as _VErr

    # Carry-forward: newest rate on/before the date wins.
    db.insert("Currency Exchange", _dict(name="GBP-USD-2026-01-01", date="2026-01-01",
                                         from_currency="GBP", to_currency="USD", exchange_rate=1.05))
    db.insert("Currency Exchange", _dict(name="GBP-USD-2026-04-01", date="2026-04-01",
                                         from_currency="GBP", to_currency="USD", exchange_rate=1.20))
    assert flt(get_exchange_rate("GBP", "USD", "2026-03-15"), 2) == 1.05, "carry-forward should pick the Jan rate"
    assert flt(get_exchange_rate("GBP", "USD", "2026-05-01"), 2) == 1.20, "carry-forward should pick the Apr rate"
    print("  Carry-forward picks the latest rate on/before the date (1.05 then 1.20)")

    # Same currency is always 1.0; inverse pair derives from 1/rate.
    assert get_exchange_rate("USD", "USD", nowdate()) == 1.0
    db.insert("Currency Exchange", _dict(name="USD-SEK-2026-01-01", date="2026-01-01",
                                         from_currency="USD", to_currency="SEK", exchange_rate=10.0))
    assert flt(get_exchange_rate("SEK", "USD", nowdate()), 2) == 0.10, "inverse pair should derive 1/10"
    print("  Same-currency -> 1.0; inverse pair derived (USD->SEK 10 => SEK->USD 0.10)")

    # Missing rate raises rather than silently assuming 1.0.
    try:
        get_exchange_rate("JPY", "USD", nowdate())
        raise AssertionError("Missing rate should have raised")
    except _VErr as err:
        assert "No exchange rate" in str(err), f"Unexpected error: {err}"
    print("  Missing rate raises (no silent 1.0)")

    # Snapshot immutability: post a NOK invoice (auto rate 1.10), then change the
    # table — the posted invoice stays at 1.10 while a new one uses the new rate.
    db.insert("Currency Exchange", _dict(name="NOK-USD-2020-01-01", date="2020-01-01",
                                         from_currency="NOK", to_currency="USD", exchange_rate=1.10))
    si_old = _SI(customer="CUST-001", company="Lambda Corp", posting_date=nowdate(),
                 currency="NOK", items=[_dict(item_code="ITEM-001", qty=1, rate=100)])
    si_old.save()
    si_old.submit()
    assert flt(si_old.conversion_rate) == 1.10, f"NOK invoice should auto-fill 1.10, got {si_old.conversion_rate}"

    def _ar_debit(voucher):
        rows = db.get_all("GL Entry", filters={"voucher_no": voucher, "is_cancelled": 0}, fields=["*"])
        return next(flt(e["debit"]) for e in rows if flt(e["debit"]) > 0)

    assert _ar_debit(si_old.name) == 110.0, f"NOK invoice base AR should be 110, got {_ar_debit(si_old.name)}"

    db.insert("Currency Exchange", _dict(name="NOK-USD-2026-05-22", date=nowdate(),
                                         from_currency="NOK", to_currency="USD", exchange_rate=9.0))
    si_new = _SI(customer="CUST-001", company="Lambda Corp", posting_date=nowdate(),
                 currency="NOK", items=[_dict(item_code="ITEM-001", qty=1, rate=100)])
    si_new.save()
    si_new.submit()
    assert flt(si_new.conversion_rate) == 9.0, f"new NOK invoice should use the new rate 9.0, got {si_new.conversion_rate}"
    assert _ar_debit(si_old.name) == 110.0, \
        f"posted invoice must not change when the rate table changes, got {_ar_debit(si_old.name)}"
    print("  Posted NOK invoice frozen at base 110.00; later invoice uses new rate 9.0 (snapshot holds)")

    # ---------------------------------------------------------------------
    # Realized FX gain/loss: settling a foreign invoice at a different rate
    # than it was booked. The party ledger (AR/AP) clears at the invoice's
    # booked base value, the bank reflects the payment-rate base, and the
    # difference posts to the Exchange Gain/Loss account.
    # ---------------------------------------------------------------------
    print_header("37. REALIZED FX - Payment Entry settling a foreign invoice")

    from lambda_erp.accounting.payment_entry import PaymentEntry as _PE

    fx_acct = db.get_value("Company", "Lambda Corp", "default_exchange_gain_loss_account")
    assert fx_acct, "company should have an Exchange Gain/Loss account"

    def _legs(voucher):
        return db.get_all("GL Entry", filters={"voucher_no": voucher, "is_cancelled": 0}, fields=["*"])

    # (a) Customer EUR invoice booked @1.10 (AR 110 base), paid 100 EUR @1.05.
    inv = _SI(customer="CUST-001", company="Lambda Corp", posting_date=nowdate(),
              currency="EUR", conversion_rate=1.10,
              items=[_dict(item_code="ITEM-001", qty=1, rate=100)])
    inv.save()
    inv.submit()
    assert flt(inv.outstanding_amount) == 100.0, "outstanding is in document currency (100 EUR)"

    pe = _PE(payment_type="Receive", party_type="Customer", party="CUST-001",
             company="Lambda Corp", posting_date=nowdate(),
             paid_amount=100, currency="EUR", conversion_rate=1.05,
             references=[_dict(reference_doctype="Sales Invoice", reference_name=inv.name, allocated_amount=100)])
    pe.save()
    pe.submit()
    assert flt(db.get_value("Sales Invoice", inv.name, "outstanding_amount")) == 0.0, "invoice should be fully settled"
    gl = _legs(pe.name)
    ar_credit = next(flt(e["credit"]) for e in gl if e.get("party") == "CUST-001")
    bank_debit = next(flt(e["debit"]) for e in gl if flt(e["debit"]) > 0 and e["account"] != fx_acct)
    fx_leg = next(e for e in gl if e["account"] == fx_acct)
    assert ar_credit == 110.0, f"AR should clear at invoice base 110, got {ar_credit}"
    assert bank_debit == 105.0, f"Bank should reflect payment base 105, got {bank_debit}"
    assert flt(fx_leg["debit"]) == 5.0 and flt(fx_leg["credit"]) == 0.0, f"FX loss 5 expected, got {dict(fx_leg)}"
    print("  Customer EUR invoice @1.10 paid @1.05: AR cleared 110, bank 105, FX LOSS 5.00")

    # (b) Same invoice setup, paid at a better rate (1.15) -> FX gain.
    inv2 = _SI(customer="CUST-001", company="Lambda Corp", posting_date=nowdate(),
               currency="EUR", conversion_rate=1.10,
               items=[_dict(item_code="ITEM-001", qty=1, rate=100)])
    inv2.save()
    inv2.submit()
    pe2 = _PE(payment_type="Receive", party_type="Customer", party="CUST-001",
              company="Lambda Corp", posting_date=nowdate(),
              paid_amount=100, currency="EUR", conversion_rate=1.15,
              references=[_dict(reference_doctype="Sales Invoice", reference_name=inv2.name, allocated_amount=100)])
    pe2.save()
    pe2.submit()
    fx_leg2 = next(e for e in _legs(pe2.name) if e["account"] == fx_acct)
    assert flt(fx_leg2["credit"]) == 5.0 and flt(fx_leg2["debit"]) == 0.0, f"FX gain 5 expected, got {dict(fx_leg2)}"
    print("  Customer EUR invoice @1.10 paid @1.15: FX GAIN 5.00")

    # (c) Supplier EUR invoice booked @1.10 (AP 110), paid 100 EUR @1.05 -> gain.
    pinv = _PI(supplier="SUPP-001", company="Lambda Corp", posting_date=nowdate(),
               currency="EUR", conversion_rate=1.10,
               items=[_dict(item_code="SVC-001", qty=1, rate=100)])
    pinv.save()
    pinv.submit()
    pe3 = _PE(payment_type="Pay", party_type="Supplier", party="SUPP-001",
              company="Lambda Corp", posting_date=nowdate(),
              paid_amount=100, currency="EUR", conversion_rate=1.05,
              references=[_dict(reference_doctype="Purchase Invoice", reference_name=pinv.name, allocated_amount=100)])
    pe3.save()
    pe3.submit()
    assert flt(db.get_value("Purchase Invoice", pinv.name, "outstanding_amount")) == 0.0, "supplier invoice should be fully settled"
    gl3 = _legs(pe3.name)
    ap_debit = next(flt(e["debit"]) for e in gl3 if e.get("party") == "SUPP-001")
    fx_leg3 = next(e for e in gl3 if e["account"] == fx_acct)
    assert ap_debit == 110.0, f"AP should clear at invoice base 110, got {ap_debit}"
    assert flt(fx_leg3["credit"]) == 5.0 and flt(fx_leg3["debit"]) == 0.0, f"FX gain 5 expected, got {dict(fx_leg3)}"
    print("  Supplier EUR invoice @1.10 paid @1.05: AP cleared 110, FX GAIN 5.00")

    # (d) Base-currency settlement posts no FX leg.
    inv_usd = _SI(customer="CUST-001", company="Lambda Corp", posting_date=nowdate(),
                  items=[_dict(item_code="ITEM-001", qty=1, rate=100)])
    inv_usd.save()
    inv_usd.submit()
    pe_usd = _PE(payment_type="Receive", party_type="Customer", party="CUST-001",
                 company="Lambda Corp", posting_date=nowdate(), paid_amount=100,
                 references=[_dict(reference_doctype="Sales Invoice", reference_name=inv_usd.name, allocated_amount=100)])
    pe_usd.save()
    pe_usd.submit()
    assert all(e["account"] != fx_acct for e in _legs(pe_usd.name)), "base-currency PE must not post an FX leg"
    assert flt(db.get_value("Sales Invoice", inv_usd.name, "outstanding_amount")) == 0.0
    print("  Base-currency (USD) payment settles with no FX leg")

    # ---------------------------------------------------------------------
    # Hold a foreign cash balance and convert it later at a broker rate. The
    # EUR bank accumulates EUR (with a base carrying value); converting relieves
    # it at its average carrying rate and realizes FX vs. the broker proceeds.
    # ---------------------------------------------------------------------
    print_header("38. MULTI-CURRENCY CASH - hold a foreign balance, convert via broker")

    from lambda_erp.accounting.general_ledger import get_account_balances

    primary_bank = "Primary Bank - LAMB"
    bank_parent = db.get_value("Account", primary_bank, "parent_account")
    db.insert("Account", _dict(
        name="EUR Bank - LAMB", account_name="EUR Bank", parent_account=bank_parent,
        company="Lambda Corp", root_type="Asset", report_type="Balance Sheet",
        account_type="Bank", account_currency="EUR", is_group=0,
    ))

    def _collect_eur(rate_pay):
        inv = _SI(customer="CUST-001", company="Lambda Corp", posting_date=nowdate(),
                  currency="EUR", conversion_rate=1.10,
                  items=[_dict(item_code="ITEM-001", qty=1, rate=100)])
        inv.save()
        inv.submit()
        pe = _PE(payment_type="Receive", party_type="Customer", party="CUST-001",
                 company="Lambda Corp", posting_date=nowdate(), paid_amount=100,
                 currency="EUR", conversion_rate=rate_pay, paid_to="EUR Bank - LAMB",
                 references=[_dict(reference_doctype="Sales Invoice", reference_name=inv.name, allocated_amount=100)])
        pe.save()
        pe.submit()

    _collect_eur(1.05)   # 100 EUR carried at 105 USD
    _collect_eur(1.08)   # 100 EUR carried at 108 USD

    eur_base, eur_ccy = get_account_balances("EUR Bank - LAMB", "Lambda Corp")
    assert flt(eur_ccy, 2) == 200.0, f"EUR bank should hold 200 EUR, got {eur_ccy}"
    assert flt(eur_base, 2) == 213.0, f"EUR bank carrying value should be 105+108=213 USD, got {eur_base}"
    print(f"  EUR bank holds {eur_ccy:.0f} EUR carried at {eur_base:.2f} USD (avg {flt(eur_base/eur_ccy,4)})")

    # Convert 150 EUR via broker @1.10 -> 165 USD into the USD bank.
    conv = _PE(payment_type="Internal Transfer", company="Lambda Corp", posting_date=nowdate(),
               paid_from="EUR Bank - LAMB", paid_amount=150,
               paid_to=primary_bank, received_amount=165)
    conv.save()
    conv.submit()
    gl = _legs(conv.name)
    usd_in = next(flt(e["debit"]) for e in gl if e["account"] == primary_bank)
    eur_out = next(flt(e["credit"]) for e in gl if e["account"] == "EUR Bank - LAMB")
    fx_leg = next(e for e in gl if e["account"] == fx_acct)
    assert usd_in == 165.0, f"USD bank should receive 165, got {usd_in}"
    assert eur_out == 159.75, f"EUR relieved at avg 1.065 (150*1.065=159.75), got {eur_out}"
    assert flt(fx_leg["credit"], 2) == 5.25 and flt(fx_leg["debit"]) == 0.0, \
        f"conversion FX gain expected 5.25 (165-159.75), got {dict(fx_leg)}"

    eur_base2, eur_ccy2 = get_account_balances("EUR Bank - LAMB", "Lambda Corp")
    assert flt(eur_ccy2, 2) == 50.0, f"EUR bank should hold 50 EUR after converting 150, got {eur_ccy2}"
    assert flt(eur_base2, 2) == 53.25, f"EUR bank carrying value should be 53.25 USD, got {eur_base2}"
    print("  Converted 150 EUR @broker 1.10 -> 165 USD; relieved EUR at avg 159.75; FX GAIN 5.25; EUR bank now 50 EUR / 53.25 USD")

    # A direct foreign->foreign conversion (no base side) is rejected.
    db.insert("Account", _dict(
        name="GBP Bank - LAMB", account_name="GBP Bank", parent_account=bank_parent,
        company="Lambda Corp", root_type="Asset", report_type="Balance Sheet",
        account_type="Bank", account_currency="GBP", is_group=0,
    ))
    try:
        bad = _PE(payment_type="Internal Transfer", company="Lambda Corp", posting_date=nowdate(),
                  paid_from="EUR Bank - LAMB", paid_amount=10, paid_to="GBP Bank - LAMB", received_amount=8)
        bad.save()
        bad.submit()
        raise AssertionError("foreign->foreign conversion should have been rejected")
    except _VErr as err:
        assert "base currency on one side" in str(err), f"Unexpected error: {err}"
    print("  Direct EUR->GBP conversion rejected (needs the base currency on one side)")

    # ---------------------------------------------------------------------
    # Period-end revaluation: open foreign monetary balances are restated to
    # the closing rate, booking *unrealized* FX. The originals are never
    # touched, and a next-day auto-reversal backs the estimate out so it
    # doesn't double-count once the balance settles.
    # ---------------------------------------------------------------------
    print_header("39. PERIOD-END REVALUATION - unrealized FX on open foreign balances")

    from lambda_erp.accounting.revaluation import run_period_revaluation
    from lambda_erp.accounting.general_ledger import get_gl_balance as _gl_balance

    period_end = nowdate()
    db.insert("Currency Exchange", _dict(name=f"EUR-USD-{period_end}", date=period_end,
                                         from_currency="EUR", to_currency="USD", exchange_rate=1.20))

    unreal_acct = db.get_value("Company", "Lambda Corp", "default_unrealized_exchange_account")
    assert unreal_acct, "company should have an Unrealized Exchange Gain/Loss account"

    # si_fx (section 35a) is an open EUR receivable booked @1.10; remember its AR leg.
    def _ar_of(voucher):
        return next(flt(e["debit"]) for e in _legs(voucher) if e.get("party") == "CUST-001" and flt(e["debit"]) > 0)
    si_fx_ar_before = _ar_of(si_fx.name)

    result = run_period_revaluation("Lambda Corp", period_end)
    assert result["posted"] and result["voucher_no"] and result["reversal_voucher_no"], \
        "revaluation should report the posted vouchers"
    by = {(ln["kind"], ln["currency"]): ln for ln in result["lines"]}

    # EUR receivable: 100 EUR open @1.10 -> closing @1.20 = unrealized gain 10.
    assert flt(by[("receivable", "EUR")]["unrealized"], 2) == 10.0, \
        f"EUR AR unrealized should be 10, got {by[('receivable','EUR')]['unrealized']}"
    # EUR cash: 50 EUR carried at 53.25 -> 50*1.20 = 60 = unrealized gain 6.75.
    assert flt(by[("cash", "EUR")]["unrealized"], 2) == 6.75, \
        f"EUR cash unrealized should be 6.75, got {by[('cash','EUR')]['unrealized']}"
    print(f"  Breakdown: EUR AR +{by[('receivable','EUR')]['unrealized']:.2f}, "
          f"EUR cash +{by[('cash','EUR')]['unrealized']:.2f} (closing 1.20)")

    # Original invoice GL is untouched — revaluation posts a separate voucher.
    assert _ar_of(si_fx.name) == si_fx_ar_before, "original invoice GL must not change on revaluation"

    # Unrealized FX is recognized at period end and reverses the next day.
    at_period_end = _gl_balance(unreal_acct, "Lambda Corp", posting_date=period_end)
    after_reversal = _gl_balance(unreal_acct, "Lambda Corp", posting_date=add_days(period_end, 1).isoformat())
    assert abs(at_period_end) > 0.01, f"unrealized FX should be recognized at period end, got {at_period_end}"
    assert abs(after_reversal) < 0.01, f"unrealized FX should net to zero after the next-day reversal, got {after_reversal}"
    print(f"  Unrealized FX at period end = {at_period_end:.2f}; nets to 0.00 after next-day reversal")

    # ---------------------------------------------------------------------
    # Presentation currency (read-only translation) + the revaluation wrapper
    # the endpoint / chat tool call.
    # ---------------------------------------------------------------------
    print_header("40. PRESENTATION CURRENCY + revaluation preview")

    from api.routers.reports import _trial_balance, _present
    from api.chat import _handle_revalue_currencies

    base_tb = _trial_balance(db, "Lambda Corp")
    eur_tb = _present(db, _trial_balance(db, "Lambda Corp"), "Lambda Corp", "EUR", period_end)
    rate = eur_tb["presentation_rate"]
    assert eur_tb["presentation_currency"] == "EUR" and rate < 1.0, \
        f"USD->EUR rate should be ~0.83 (inverse of 1.20), got {rate}"
    assert flt(eur_tb["difference"], 2) == 0.0, "a translated trial balance must still balance"
    assert flt(eur_tb["total_debit"], 2) == flt(base_tb["total_debit"] * rate, 2), \
        "totals should scale by the presentation rate"
    # Presenting in the base currency is a 1.0 no-op (identity).
    usd_tb = _present(db, _trial_balance(db, "Lambda Corp"), "Lambda Corp", "USD", period_end)
    assert usd_tb["presentation_rate"] == 1.0 and flt(usd_tb["total_debit"], 2) == flt(base_tb["total_debit"], 2), \
        "presenting in the base currency must be an identity translation"
    print(f"  Trial balance presented in EUR @ {rate}: total debit {base_tb['total_debit']:.2f} USD -> {eur_tb['total_debit']:.2f} EUR (still balances)")

    # The chat/endpoint wrapper: preview (post=false) returns the breakdown, no posting.
    preview = _handle_revalue_currencies({"company": "Lambda Corp", "date": period_end, "post": False})
    assert preview["posted"] is False, "preview must not post"
    assert ("receivable", "EUR") in {(ln["kind"], ln["currency"]) for ln in preview["lines"]}, \
        "preview should list the open EUR receivable"
    assert "net_unrealized_pl" in preview
    print(f"  revalue_currencies preview: net unrealized P&L {preview['net_unrealized_pl']:.2f} (nothing posted)")

    # AR aging aggregates in base currency: the open EUR invoice (si_fx, 100 EUR
    # booked @1.10) ages at its base value 110, not its document-currency 100.
    from api.routers.reports import _ar_aging
    aging = _ar_aging(db, "Lambda Corp")
    fx_row = next((r for r in aging["rows"] if r["invoice"] == si_fx.name), None)
    assert fx_row is not None, "open EUR invoice should appear in AR aging"
    assert flt(fx_row["outstanding"], 2) == 110.0, \
        f"EUR invoice should age at its base value 110 (100 EUR x 1.10), got {fx_row['outstanding']}"
    print(f"  AR aging shows the EUR invoice at base value {fx_row['outstanding']:.2f} (not doc-currency 100)")

    # Dashboard summary runs and aggregates receivables in base currency.
    from api.routers.reports import _dashboard_summary
    dash = _dashboard_summary(db, "Lambda Corp")
    assert flt(dash["outstanding_receivable"], 2) > 0, "dashboard receivable should be positive"
    print(f"  Dashboard outstanding receivable (base): {dash['outstanding_receivable']:.2f}")

    # =====================================================================
    print_header("41. STOCK MOVEMENTS dataset - most-moved item by volume")
    # =====================================================================
    # The chat answers "what stock item is moved around the most" by running
    # query_dataset over the stock_movements semantic dataset. Verify the
    # dataset's GROUP BY aggregation matches an independent ledger query.
    from api.routers.analytics import aggregate_semantic_dataset

    agg = aggregate_semantic_dataset(
        dataset="stock_movements",
        group_by=["item_code"],
        measures={"qty_moved": ["sum", "abs_qty"], "moves": ["count"]},
        order_by=[{"field": "qty_moved", "direction": "desc"}],
    )
    assert agg["rows"], "stock_movements aggregation returned no rows"

    # Independent truth: sum of |actual_qty| per item over non-cancelled SLEs.
    expected = db.sql(
        'SELECT item_code, SUM(ABS(actual_qty)) AS qty_moved, COUNT(*) AS moves '
        'FROM "Stock Ledger Entry" WHERE is_cancelled = 0 '
        'GROUP BY item_code ORDER BY qty_moved DESC, item_code ASC'
    )
    top = agg["rows"][0]
    exp_top = expected[0]
    assert top["item_code"] == exp_top["item_code"], (
        f"most-moved item should be {exp_top['item_code']}, got {top['item_code']}"
    )
    assert flt(top["qty_moved"], 2) == flt(exp_top["qty_moved"], 2), (
        f"top item volume mismatch: dataset {top['qty_moved']} vs ledger {exp_top['qty_moved']}"
    )
    assert int(top["moves"]) == int(exp_top["moves"]), "movement count mismatch on top item"

    # abs_qty is volume (never negative); the default_where must drop cancelled
    # legs, so the dataset's total can't exceed the raw ledger's gross volume.
    assert all(flt(r["qty_moved"]) >= 0 for r in agg["rows"]), "abs_qty volume must be non-negative"
    print(
        f"  Most-moved item: {top['item_code']} "
        f"({flt(top['qty_moved'], 2)} units across {int(top['moves'])} movements)"
    )

    # ---------------------------------------------------------------------
    # Extension seam: a customer repo overrides a document class via the
    # registry and registers lifecycle hooks — without editing core files.
    # ---------------------------------------------------------------------
    print_header("42. EXTENSION SEAM - registry override + lifecycle hooks")

    from api import services as _svc
    from lambda_erp.hooks import register_hook, clear_hooks
    from lambda_erp.accounting.sales_invoice import SalesInvoice as _BaseSI

    _orig_si = _svc.DOCUMENT_CLASSES["Sales Invoice"]
    sub_calls = []

    class _PluginSI(_BaseSI):
        def on_submit(self):
            sub_calls.append("override")
            super().on_submit()

    def _new_si():
        d = _svc.create_document("sales-invoice", {
            "customer": "CUST-001", "company": "Lambda Corp", "posting_date": nowdate(),
            "items": [_dict(item_code="ITEM-001", qty=1, rate=100)],
        })
        return d["name"]

    try:
        # (a) registry override is resolved by the loader path.
        _svc.register_doctype("Sales Invoice", _PluginSI)
        assert _svc.get_document_class("sales-invoice")[1] is _PluginSI, "registry should return the override"
        n1 = _new_si()
        _svc.submit_document("sales-invoice", n1)
        assert "override" in sub_calls, "the registered subclass on_submit should run via create/submit"
        print("  registry override used by create/submit:", sub_calls)

        # (b) after_submit hook fires post-commit.
        events = []
        register_hook("Sales Invoice:after_submit", lambda doc: events.append(doc.name))
        n2 = _new_si()
        _svc.submit_document("sales-invoice", n2)
        assert events == [n2], f"after_submit should fire once for the submitted doc, got {events}"
        print("  after_submit hook fired for", events)

        # (c) raising before_submit aborts and rolls back to draft.
        clear_hooks()
        register_hook("Sales Invoice:before_submit", lambda doc: (_ for _ in ()).throw(ValueError("blocked")))
        n3 = _new_si()
        try:
            _svc.submit_document("sales-invoice", n3)
            raise AssertionError("raising before_submit should have aborted the submit")
        except ValueError:
            pass
        assert flt(db.get_value("Sales Invoice", n3, "docstatus")) == 0, "aborted submit must stay draft"
        print("  raising before_submit aborted submit; docstatus stayed draft")
    finally:
        _svc.register_doctype("Sales Invoice", _orig_si)  # restore core class
        clear_hooks()

    # =====================================================================
    # FINAL SUMMARY
    # =====================================================================
    print_header("TRIAL BALANCE")

    all_accounts = db.get_all(
        "Account",
        filters={"company": "Lambda Corp", "is_group": 0},
        fields=["name", "root_type", "report_type"],
        order_by="root_type, name",
    )

    print(f"  {'Account':<40} {'Debit':>12} {'Credit':>12} {'Balance':>12}")
    print(f"  {'-'*40} {'-'*12} {'-'*12} {'-'*12}")

    total_debit = 0
    total_credit = 0

    for account in all_accounts:
        entries = db.get_all(
            "GL Entry",
            filters={"account": account["name"], "is_cancelled": 0},
            fields=["debit", "credit"],
        )
        if not entries:
            continue

        debit = sum(flt(e["debit"]) for e in entries)
        credit = sum(flt(e["credit"]) for e in entries)
        balance = debit - credit

        if debit or credit:
            acct_name = account["name"][:38] if len(account["name"]) > 40 else account["name"]
            print(f"  {acct_name:<40} {fmt_money(debit):>12} {fmt_money(credit):>12} {fmt_money(balance):>12}")
            total_debit += debit
            total_credit += credit

    print(f"  {'-'*40} {'-'*12} {'-'*12} {'-'*12}")
    print(f"  {'TOTAL':<40} {fmt_money(total_debit):>12} {fmt_money(total_credit):>12} {fmt_money(total_debit - total_credit):>12}")

    # Hard gate: after every flow above (incl. foreign-currency sales, bills,
    # settlement FX, conversions, and revaluation) the whole ledger must still
    # net to zero. A failure here means the books don't add up.
    assert abs(total_debit - total_credit) < 0.01, \
        f"Trial balance does not balance — off by {fmt_money(total_debit - total_credit)}"
    print(f"\n  BALANCED - Double-entry bookkeeping integrity verified!")

    print(f"\n{'='*60}")
    print(f"  Demo complete! All core ERP flows exercised.")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
