"""Test composition only; permanent API/worker wiring belongs to A4."""

from app.api.events import router
from app.main import create_app


def create_live_app():
    return create_app(routers=(router,))
