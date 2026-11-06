"""Read folder observations using application node UUIDs."""

from uuid import UUID

from fastapi import APIRouter

from app.api.nodes import Limit, Offset, Registry
from app.contracts import dto

router = APIRouter()


@router.get(
    "/nodes/{node_id}/folders",
    response_model=dto.FolderPage,
    operation_id="list_folders",
    responses={code: {"model": dto.Error} for code in (401, 404, 422, 503)},
)
def folders(node_id: UUID, service: Registry, limit: Limit = 50, offset: Offset = 0):
    return service.folders(node_id, limit, offset)
