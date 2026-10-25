"""One real collection pass in a fresh process, restricted to the owned test DB."""

import argparse
import json
import os
from dataclasses import asdict
from uuid import UUID

from app.collection import CollectionService
from app.config import Settings
from app.contracts.interfaces import SystemClock
from app.db import Database
from sqlalchemy.engine import make_url


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node", type=UUID, action="append")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    url = make_url(os.environ.get("SCC_COLLECTION_TEST_DATABASE_URL", ""))
    if not (
        os.environ.get("SCC_COLLECTION_TEST_PRIVATE_CLUSTER") == "a32-private-cluster"
        and os.environ.get("SCC_DATABASE_URL") == url.render_as_string(hide_password=False)
        and url.host == "127.0.0.1"
        and url.database == "scc_a32_tests"
        and url.username == "scc_test"
        and url.port not in {None, 5432}
    ):
        raise RuntimeError("Use the private collection PostgreSQL runner")
    settings = Settings()
    database = Database(settings)
    try:
        database.check_ready()
        service = CollectionService(settings, database, SystemClock())
        results = (
            tuple(service.collect_node(node) for node in args.node)
            if args.node
            else service.collect_once(force=args.force)
        )
        print(json.dumps([asdict(result) for result in results], default=str))
    finally:
        database.close()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Never print a traceback containing environment or SQL parameters.
        print("Private collection pass failed")
        raise SystemExit(1) from None
