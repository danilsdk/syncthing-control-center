"""Explicit migrations; CLI settings and test-provided connections share metadata."""

from alembic import context
from app.config import Settings
from app.db import Database
from app.models import Base

config = context.config
target_metadata = Base.metadata


def run_with_connection(connection):
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    settings = Settings()
    if settings.database_url is None:
        raise RuntimeError("SCC_DATABASE_URL must be configured explicitly")
    context.configure(
        url=settings.database_url.get_secret_value(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()
elif config.attributes.get("connection") is not None:
    run_with_connection(config.attributes["connection"])
else:
    database = Database(Settings())
    if database.engine is None:
        raise RuntimeError("SCC_DATABASE_URL must be configured explicitly")
    try:
        with database.engine.connect() as connection:
            run_with_connection(connection)
    finally:
        database.close()
