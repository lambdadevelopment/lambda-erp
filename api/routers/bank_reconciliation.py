"""Bank reconciliation queue, suggestions, posting, matching, and reversal."""

from fastapi import APIRouter, Depends, Query

from api.auth import require_role
from lambda_erp.accounting.bank_reconciliation import (
    list_bank_transactions,
    reconcile_with_existing_voucher,
    reconcile_with_existing_voucher_group,
    reconcile_with_journal,
    reconcile_with_payment,
    suggest_matches,
    undo_reconciliation,
)
from lambda_erp.accounting.subscription import Subscription


router = APIRouter(tags=["bank-reconciliation"], dependencies=[Depends(require_role("manager"))])


@router.get("/bank-reconciliation/transactions")
def transactions(
    status: str = "Unreconciled",
    limit: int = Query(default=100, ge=1, le=500),
):
    return list_bank_transactions(status=status, limit=limit)


@router.get("/bank-reconciliation/transactions/{bank_transaction}/suggestions")
def suggestions(
    bank_transaction: str,
    limit: int = Query(default=12, ge=1, le=50),
):
    return suggest_matches(bank_transaction, limit=limit)


@router.post("/bank-reconciliation/payment")
def post_payment(data: dict, user: dict = Depends(require_role("manager"))):
    return reconcile_with_payment(
        data.get("bank_transaction"),
        data.get("allocations") or [],
        conversion_rate=data.get("conversion_rate"),
        user=user.get("name"),
        confirmed=data.get("confirmed") is True,
    )


@router.post("/bank-reconciliation/journal")
def post_journal(data: dict, user: dict = Depends(require_role("manager"))):
    return reconcile_with_journal(
        data.get("bank_transaction"),
        data.get("counterparty_account"),
        conversion_rate=data.get("conversion_rate"),
        remarks=data.get("remarks"),
        user=user.get("name"),
        confirmed=data.get("confirmed") is True,
    )


@router.post("/bank-reconciliation/match-existing")
def match_existing(data: dict, user: dict = Depends(require_role("manager"))):
    bank_transactions = data.get("bank_transactions")
    if bank_transactions:
        return reconcile_with_existing_voucher_group(
            bank_transactions,
            data.get("voucher_type"),
            data.get("voucher_no"),
            user=user.get("name"),
            confirmed=data.get("confirmed") is True,
        )
    return reconcile_with_existing_voucher(
        data.get("bank_transaction"),
        data.get("voucher_type"),
        data.get("voucher_no"),
        user=user.get("name"),
        confirmed=data.get("confirmed") is True,
    )


@router.post("/bank-reconciliation/undo")
def undo(data: dict, user: dict = Depends(require_role("manager"))):
    return undo_reconciliation(
        data.get("bank_transaction"),
        user=user.get("name"),
        confirmed=data.get("confirmed") is True,
    )


# Compatibility for clients that used the old one-reference endpoint. It now
# performs the safe existing-voucher path and deliberately rejects invoices:
# an invoice alone is not a bank posting.
@router.post("/bank-reconciliation/match")
def match_transaction(data: dict, user: dict = Depends(require_role("manager"))):
    return reconcile_with_existing_voucher(
        data.get("bank_transaction"),
        data.get("reference_doctype"),
        data.get("reference_name"),
        user=user.get("name"),
        confirmed=data.get("confirmed") is True,
    )


@router.post("/documents/subscription/{name}/process")
def process_subscription(name: str):
    """Process a subscription to generate the next invoice if due."""
    sub = Subscription.load(name)
    result = sub.process()
    if result:
        return {"status": "invoice_created", "invoice": result}
    return {"status": "no_invoice_due"}
