"""Stock actions that are more than a single document save, available over
REST (/api/actions/...), chat and MCP."""
from pydantic import Field

from api import mentions, services
from lambda_erp import permissions
from lambda_erp.stock.stock_entry import make_location_move


class MoveLocation(mentions.Input):
    warehouse: str = Field(min_length=1, max_length=200)
    from_location: str = Field(min_length=1, max_length=200)
    to_location: str | None = Field(default=None, max_length=200)
    to_warehouse: str | None = Field(default=None, max_length=200)


def prepare_location_move(args):
    data = MoveLocation.model_validate(args)
    permissions.require_writable("Stock Entry", "create", keeps_hidden_fields=True)
    permissions.require_warehouse_in_scope(data.warehouse)
    permissions.require_warehouse_in_scope(data.to_warehouse, optional=True)
    entry = make_location_move(data.warehouse, data.from_location, data.to_location, data.to_warehouse)
    entry.save()
    return services.load_document("stock-entry", entry.name)


class ReserveOrder(mentions.Input):
    sales_order: str = Field(min_length=1, max_length=200)
    sales_order_items: list[str] | None = Field(default=None, max_length=500)


class SuggestPicks(mentions.Input):
    item_code: str = Field(min_length=1, max_length=200)
    warehouse: str = Field(min_length=1, max_length=200)
    qty: float = Field(gt=0)
    customer: str | None = Field(default=None, max_length=200)
    sales_order_item: str | None = Field(default=None, max_length=200)
    include_short_dated: bool = False


def reserve_order(args):
    data = ReserveOrder.model_validate(args)
    permissions.require_readable("Sales Order", data.sales_order)
    permissions.require_writable("Stock Reservation", "create", keeps_hidden_fields=True)
    from lambda_erp.stock.reservation import reserve_sales_order
    return reserve_sales_order(data.sales_order, data.sales_order_items)


def suggest(args):
    data = SuggestPicks.model_validate(args)
    permissions.require("Stock Reservation", "read")
    permissions.require_warehouse_in_scope(data.warehouse)
    permissions.require_in_scope("customer", data.customer, optional=True)
    from lambda_erp.stock.reservation import suggest_picks
    return suggest_picks(data.item_code, data.warehouse, data.qty, customer=data.customer,
                         sales_order_item=data.sales_order_item, include_short_dated=data.include_short_dated)


class PrepareCount(mentions.Input):
    warehouse: str = Field(min_length=1, max_length=200)
    location: str | None = Field(default=None, max_length=200)
    item_code: str | None = Field(default=None, max_length=200)
    item_prefix: str | None = Field(default=None, max_length=200)
    stock_count: str | None = Field(default=None, max_length=200)


def prepare_count(args):
    data = PrepareCount.model_validate(args)
    from lambda_erp.stock.stock_count import prepare_stock_count
    if data.stock_count:
        permissions.require_writable("Stock Count", "update", keeps_hidden_fields=True)
        permissions.require_readable("Stock Count", data.stock_count)
    else:
        permissions.require_writable("Stock Count", "create", keeps_hidden_fields=True)
    permissions.require_warehouse_in_scope(data.warehouse)
    doc = prepare_stock_count(data.warehouse, location=data.location, item_code=data.item_code,
                              item_prefix=data.item_prefix, stock_count=data.stock_count)
    doc.save()
    return services.load_document("stock-count", doc.name)


class ConsignmentLine(mentions.Input):
    item_code: str = Field(min_length=1, max_length=200)
    qty: float | None = Field(default=None, gt=0)
    batch_no: str | None = Field(default=None, max_length=200)
    location: str | None = Field(default=None, max_length=200)
    rate: float | None = Field(default=None, ge=0)


class RecordWithdrawal(mentions.Input):
    warehouse: str = Field(min_length=1, max_length=200)
    items: list[ConsignmentLine] = Field(min_length=1, max_length=500)
    external_source: str | None = Field(default=None, max_length=200)
    external_reference: str | None = Field(default=None, max_length=200)
    posting_date: str | None = Field(default=None, max_length=10)
    replenish: bool = False
    replenish_from_warehouse: str | None = Field(default=None, max_length=200)
    remarks: str | None = Field(default=None, max_length=2000)


class PrepareStocking(mentions.Input):
    warehouse: str = Field(min_length=1, max_length=200)
    items: list[ConsignmentLine] = Field(min_length=1, max_length=500)
    from_warehouse: str | None = Field(default=None, max_length=200)


class PrepareRecall(mentions.Input):
    warehouse: str = Field(min_length=1, max_length=200)
    to_warehouse: str = Field(min_length=1, max_length=200)
    to_location: str | None = Field(default=None, max_length=200)
    items: list[ConsignmentLine] | None = Field(default=None, max_length=500)


def record_consignment_withdrawal(args):
    data = RecordWithdrawal.model_validate(args)
    permissions.require_writable("Consignment Withdrawal", "create", keeps_hidden_fields=True)
    permissions.require("Consignment Withdrawal", "submit")
    permissions.require_writable("Sales Invoice", "create", keeps_hidden_fields=True)
    permissions.require_warehouse_in_scope(data.warehouse)
    permissions.require_warehouse_in_scope(data.replenish_from_warehouse, optional=True)
    from lambda_erp.stock.consignment import record_withdrawal
    for line in data.items:
        if line.qty is None:
            raise ValueError("Every withdrawal line needs a qty")
    return record_withdrawal(
        data.warehouse, [line.model_dump(exclude_none=True) for line in data.items],
        external_source=data.external_source, external_reference=data.external_reference,
        posting_date=data.posting_date, replenish=data.replenish,
        replenish_from_warehouse=data.replenish_from_warehouse, remarks=data.remarks)


def prepare_consignment_stocking(args):
    data = PrepareStocking.model_validate(args)
    permissions.require_writable("Stock Entry", "create", keeps_hidden_fields=True)
    permissions.require_warehouse_in_scope(data.warehouse)
    permissions.require_warehouse_in_scope(data.from_warehouse, optional=True)
    from lambda_erp.stock.consignment import make_consignment_stocking
    entry = make_consignment_stocking(
        data.warehouse, [line.model_dump(exclude_none=True) for line in data.items], from_warehouse=data.from_warehouse)
    entry.save()
    return services.load_document("stock-entry", entry.name)


def prepare_consignment_recall(args):
    data = PrepareRecall.model_validate(args)
    permissions.require_writable("Stock Entry", "create", keeps_hidden_fields=True)
    permissions.require_warehouse_in_scope(data.warehouse)
    permissions.require_warehouse_in_scope(data.to_warehouse)
    from lambda_erp.stock.consignment import make_consignment_recall
    entry = make_consignment_recall(
        data.warehouse, data.to_warehouse, to_location=data.to_location,
        items=[line.model_dump(exclude_none=True) for line in data.items] if data.items else None)
    entry.save()
    return services.load_document("stock-entry", entry.name)


class ConfirmPurchaseOrder(mentions.Input):
    purchase_order: str = Field(min_length=1, max_length=200)
    confirmed_date: str = Field(min_length=8, max_length=10)
    lines: list[str] | None = Field(default=None, max_length=500)
    confirmed_qty: float | None = Field(default=None, gt=0)
    confirmation_reference: str | None = Field(default=None, max_length=200)


def confirm_purchase_order(args, user=None):
    data = ConfirmPurchaseOrder.model_validate(args)
    permissions.require_readable("Purchase Order", data.purchase_order)
    permissions.require("Purchase Order", "update")
    from lambda_erp.backlog import confirm_purchase_order_lines
    return confirm_purchase_order_lines(
        data.purchase_order, data.confirmed_date, lines=data.lines, confirmed_qty=data.confirmed_qty,
        confirmation_reference=data.confirmation_reference,
        confirmed_by=(user or {}).get("user_id") or (user or {}).get("name"))


def initialize_stock_actions():
    services.register_action(
        "prepare_stock_count", prepare_count,
        description=(
            "Create a DRAFT Stock Count for a warehouse (an own one or a customer's consignment warehouse) with one "
            "line per item, batch and storage location and the quantity the system expects (system_qty); optionally "
            "only one location, one item_code or an item_prefix. The counter then enters counted_qty per line with "
            "update_document. Saving changes no stock; submitting posts the differences and needs a reason. With "
            "stock_count (an existing draft) it refreshes the system quantities instead and keeps what was counted."),
        parameters=PrepareCount.model_json_schema(), minimum_role="manager")
    services.register_action(
        "record_consignment_withdrawal", record_consignment_withdrawal,
        description=(
            "Record what a customer reported as USED from their consignment warehouse and bill it: creates and "
            "submits a Consignment Withdrawal and ONE Sales Invoice that also takes the goods out of that warehouse. "
            "items: item_code, qty, batch_no as reported (and location if the warehouse uses storage locations). "
            "Give external_source + external_reference (the customer's report / order number) whenever there is one: "
            "the same report sent again then returns the first result (replayed=true) instead of billing twice, and "
            "the same number with different content is refused. replenish=true also drafts a replacement stocking."),
        parameters=RecordWithdrawal.model_json_schema(), minimum_role="manager")
    services.register_action(
        "prepare_consignment_stocking", prepare_consignment_stocking,
        description=(
            "Draft the stocking of a customer's consignment warehouse: a DRAFT Stock Entry (Material Transfer) from "
            "from_warehouse (default: the warehouse's replenishment_warehouse) with batches proposed first-expired-"
            "first-out. No sale and no invoice. Nothing moves until the draft is submitted."),
        parameters=PrepareStocking.model_json_schema(), minimum_role="manager")
    services.register_action(
        "prepare_consignment_recall", prepare_consignment_recall,
        description=(
            "Draft bringing goods back from a customer's consignment warehouse to one of your own (to_warehouse): "
            "everything in it, or the given items / batches. A DRAFT Stock Entry; no credit note, the goods were "
            "never sold. Nothing moves until the draft is submitted."),
        parameters=PrepareRecall.model_json_schema(), minimum_role="manager")
    services.register_action(
        "confirm_purchase_order", confirm_purchase_order,
        description=(
            "Record the supplier's confirmed delivery date on a SUBMITTED purchase order: on all open lines, or on "
            "`lines` (row names or positions). Optional confirmation_reference (the supplier's confirmation number) "
            "and, for one line, confirmed_qty. Use it when an order confirmation arrives; the date then shows in the "
            "sales-backlog and purchase-backlog reports. Only assign a confirmation to an order when the order number "
            "is stated or the user confirms which order it belongs to."),
        parameters=ConfirmPurchaseOrder.model_json_schema(), minimum_role="manager", pass_user=True)
    services.register_action(
        "reserve_sales_order", reserve_order,
        description=(
            "Reserve stock for the undelivered part of a submitted sales order (all stock lines, or only "
            "sales_order_items). Reserves as much as is available per line and reports short_qty for the rest. "
            "Reserved stock cannot be delivered, issued or transferred away for anyone else. To give stock back, "
            "update the Stock Reservation's status to Released."),
        parameters=ReserveOrder.model_json_schema(), minimum_role="manager")
    services.register_action(
        "suggest_picks", suggest,
        description=(
            "READ-ONLY picking proposal, first expired first out: which batches and storage locations to take a "
            "quantity of an item from in a warehouse. Never proposes blocked or expired batches or stock reserved "
            "for other orders; batches with too little shelf life left are listed under excluded unless "
            "include_short_dated. Changes nothing: no reservation, no stock movement, no invoice."),
        parameters=SuggestPicks.model_json_schema(), minimum_role="viewer")
    services.register_action(
        "prepare_location_move", prepare_location_move,
        description=(
            "Prepare moving EVERYTHING that lies in one storage location to another location "
            "(same warehouse, or to_warehouse): creates a DRAFT Stock Entry (Material Transfer) with one row "
            "per item and batch found there. Nothing moves until that draft is submitted with submit_document; "
            "show the rows to the user first. For single items create a Material Transfer directly."),
        parameters=MoveLocation.model_json_schema(), minimum_role="manager")
