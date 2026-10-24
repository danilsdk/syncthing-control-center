import os

import pytest


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch):
    # Test factories never inherit a developer's real database or connection keys.
    for name in tuple(os.environ):
        if name.startswith("SCC_"):
            monkeypatch.delenv(name)
