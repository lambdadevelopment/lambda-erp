"""Bank reconciliation API endpoints."""

from fastapi import APIRouter, Depends
from lambda_erp.accounting.bank_transaction import reconcile_bank_transaction
from lambda_erp.accounting.subscription import Subscription
from api.auth import require_role

router = APIRouter(tags=["bank-reconciliation"], dependencies=[Depends(require_role("manager"))])


@router.post("/bank-reconciliation/match")
def match_transaction(data: dict):
    """Match a bank transaction to a payment entry or invoice."""
    bt_name = data.get("bank_transaction")
    ref_doctype = data.get("reference_doctype")
    ref_name = data.get("reference_name")

    if not bt_name or not ref_doctype or not ref_name:
        return {"detail": "bank_transaction, reference_doctype, and reference_name are required"}

    return reconcile_bank_transaction(bt_name, ref_doctype, ref_name)


@router.post("/documents/subscription/{name}/process")
def process_subscription(name: str):
    """Process a subscription to generate the next invoice if due."""
    sub = Subscription.load(name)
    result = sub.process()
    if result:
        return {"status": "invoice_created", "invoice": result}
    return {"status": "no_invoice_due"}
