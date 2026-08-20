"""REST surface for deployment-registered business actions."""

from fastapi import APIRouter, Depends, HTTPException

from api import services
from api.auth import require_role


router = APIRouter(prefix="/actions", tags=["actions"])
_viewer = Depends(require_role("viewer"))


@router.post("/{action_name}")
def run_action(action_name: str, data: dict, user: dict = _viewer):
    """Execute the same registered action exposed to chat and MCP."""
    try:
        return services.run_registered_action(action_name, data, user)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown action: {action_name}")
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
