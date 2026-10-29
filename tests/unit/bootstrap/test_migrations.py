from io import StringIO
from pathlib import Path
from unittest.mock import Mock

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine


def test_offline_migration_is_complete_without_connecting(monkeypatch):
    root = Path(__file__).resolve().parents[3]
    monkeypatch.setenv("SCC_DATABASE_URL", "postgresql+psycopg://test:unused@127.0.0.1/test")
    connect = Mock(side_effect=AssertionError("Offline migration must not connect"))
    monkeypatch.setattr(Engine, "connect", connect)
    output = StringIO()
    config = Config(str(root / "backend/alembic.ini"), output_buffer=output)
    command.upgrade(config, "head", sql=True)
    sql = output.getvalue()
    for name in [
        "app_users",
        "managed_nodes",
        "folders",
        "sync_rules",
        "command_executions",
        "sync_events",
        "status_snapshots",
    ]:
        assert f"CREATE TABLE {name}" in sql
    assert "CREATE UNIQUE INDEX uq_sync_rules_active_folder" in sql
    assert "CREATE FUNCTION scc_touch_updated_at" in sql
    connect.assert_not_called()
