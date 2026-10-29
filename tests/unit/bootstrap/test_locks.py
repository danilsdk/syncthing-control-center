from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from app.contracts.locks import AdvisoryLocks, LockLost, folder_lock_key
from sqlalchemy import Connection, Engine
from sqlalchemy.exc import OperationalError


@pytest.mark.parametrize("timeout", [-1, 31, float("nan"), float("inf")])
def test_invalid_waits_are_rejected_before_connecting(timeout):
    engine = MagicMock(spec=Engine)
    with pytest.raises(ValueError), AdvisoryLocks(engine).folder(uuid4(), timeout_seconds=timeout):
        pytest.fail("Invalid timeout must not acquire a connection")
    engine.connect.assert_not_called()


def test_unlock_failure_discards_the_physical_connection():
    engine = MagicMock(spec=Engine)
    engine.dialect = SimpleNamespace(name="postgresql")
    connection = MagicMock(spec=Connection)
    connection.closed = False
    connection.invalidated = False
    engine.connect.return_value = connection
    connection.execute.return_value.one.return_value = (True, 123)
    connection.scalar.side_effect = OperationalError("unlock", None, Exception("db-secret-canary"))
    with pytest.raises(LockLost) as error, AdvisoryLocks(engine).folder(uuid4()):
        pass
    assert "db-secret-canary" not in str(error.value)
    connection.invalidate.assert_called_once_with()
    connection.close.assert_called_once_with()


def test_folder_key_uses_a_stable_signed_integer_and_internal_uuid_only():
    folder = UUID("22222222-2222-4222-8222-222222222222")
    assert -(1 << 63) <= folder_lock_key(folder) < (1 << 63)
    assert folder_lock_key(folder) == folder_lock_key(UUID(str(folder)))
    assert folder_lock_key(uuid4()) != folder_lock_key(folder)
    with pytest.raises(TypeError):
        folder_lock_key(str(folder))
