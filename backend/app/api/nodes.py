"""Registry endpoints; attached under the shared administrator guard."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from app.api.auth import CSRF_PARAMETER, ORIGIN_PARAMETER
from app.contracts import dto
from app.registry.service import RegistryService

router = APIRouter()
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0)]


def get_registry(request: Request) -> RegistryService:
    return request.app.state.registry


Registry = Annotated[RegistryService, Depends(get_registry)]


@router.get(
    "/connections",
    response_model=dto.ConnectionPage,
    operation_id="list_connections",
    responses={code: {"model": dto.Error} for code in (401, 422, 503)},
)
def connections(service: Registry, limit: Limit = 50, offset: Offset = 0):
    return service.connections(limit, offset)


@router.get(
    "/nodes",
    response_model=dto.NodePage,
    operation_id="list_nodes",
    responses={code: {"model": dto.Error} for code in (401, 422, 503)},
)
def nodes(service: Registry, limit: Limit = 50, offset: Offset = 0):
    return service.nodes(limit, offset)


@router.post(
    "/nodes",
    response_model=dto.Node,
    status_code=201,
    operation_id="register_node",
    responses={code: {"model": dto.Error} for code in (401, 403, 409, 422, 502, 503, 504)},
    openapi_extra={"parameters": [ORIGIN_PARAMETER, CSRF_PARAMETER]},
)
def register(payload: dto.NodeCreate, service: Registry):
    return service.register(payload)


@router.get(
    "/nodes/{node_id}",
    response_model=dto.Node,
    operation_id="get_node",
    responses={code: {"model": dto.Error} for code in (401, 404, 422, 503)},
)
def node(node_id: UUID, service: Registry):
    return service.node(node_id)


@router.patch(
    "/nodes/{node_id}",
    response_model=dto.Node,
    operation_id="update_node",
    responses={code: {"model": dto.Error} for code in (401, 403, 404, 409, 422, 503)},
    openapi_extra={"parameters": [ORIGIN_PARAMETER, CSRF_PARAMETER]},
)
def patch(node_id: UUID, payload: dto.NodePatch, service: Registry):
    return service.patch(node_id, payload)
