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


def initialize_stock_actions():
    services.register_action(
        "prepare_location_move", prepare_location_move,
        description=(
            "Prepare moving EVERYTHING that lies in one storage location to another location "
            "(same warehouse, or to_warehouse): creates a DRAFT Stock Entry (Material Transfer) with one row "
            "per item and batch found there. Nothing moves until that draft is submitted with submit_document; "
            "show the rows to the user first. For single items create a Material Transfer directly."),
        parameters=MoveLocation.model_json_schema(), minimum_role="manager")
