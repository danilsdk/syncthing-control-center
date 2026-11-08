from typing import Annotated
from uuid import uuid4

import pytest
from app.api.auth import require_admin
from app.api.dependencies import get_adapter
from app.config import Settings
from app.contracts.dto import Error
from app.contracts.interfaces import AdapterError, AdapterErrorCode, SyncthingAdapter
from app.main import create_app
from app.syncthing_adapter import HttpAdapterFactory
from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient


@pytest.mark.parametrize(
    "failure,status,code",
    [
        (KeyError("resolver-secret-canary"), 404, "not_found"),
        (RuntimeError("resolver-secret-canary"), 503, "service_unavailable"),
        *[
            (
                AdapterError(error),
                404 if error == "not_found" else 503,
                "not_found" if error == "not_found" else "service_unavailable",
            )
            for error in AdapterErrorCode
        ],
    ],
)
def test_resolver_errors_are_safe_api_responses_before_the_route(failure, status, code):
    def resolve(_node_id):
        raise failure

    router = APIRouter()

    @router.get("/nodes/{node_id}/test-adapter")
    def protected(adapter: Annotated[SyncthingAdapter, Depends(get_adapter)]):
        pytest.fail("A failed resolver must not reach the route")

    app = create_app(Settings(), routers=[router], adapters=HttpAdapterFactory(resolve))
    app.dependency_overrides[require_admin] = lambda: None
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get(f"/api/nodes/{uuid4()}/test-adapter")
    error = Error.model_validate(response.json()).error
    assert response.status_code == status
    assert error.code == code
    assert str(error.request_id) == response.headers["X-Request-ID"]
    assert "resolver-secret-canary" not in response.text
