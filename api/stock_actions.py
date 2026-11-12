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
    permissions.require_writable("Stock Entry", "create")
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
    permissions.require_writable("Stock Reservation", "create")
    from lambda_erp.stock.reservation import reserve_sales_order
    return reserve_sales_order(data.sales_order, data.sales_order_items)


def suggest(args):
    data = SuggestPicks.model_validate(args)
    permissions.require("Stock Reservation", "read")
    from lambda_erp.stock.reservation import suggest_picks
    return suggest_picks(data.item_code, data.warehouse, data.qty, customer=data.customer,
                         sales_order_item=data.sales_order_item, include_short_dated=data.include_short_dated)


def initialize_stock_actions():
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
