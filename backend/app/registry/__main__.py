"""Local operator command; passwords are never accepted in command arguments."""

import argparse
import getpass
import sys

from sqlalchemy.exc import SQLAlchemyError

from app.config import Settings
from app.db import Database, DatabaseNotReady
from app.registry.admin import BootstrapError, create_administrator


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        # Unknown arguments might be an accidentally supplied password.
        self.print_usage(sys.stderr)
        self.exit(2, "Invalid command arguments; use --help\n")


def main(argv: list[str] | None = None) -> int:
    parser = SafeArgumentParser(description="Local SCC administrator bootstrap")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("admin-create")
    create.add_argument("--login", required=True)
    args = parser.parse_args(argv)
    database = None
    result = 1
    try:
        database = Database(Settings())
        database.check_ready()
        password = getpass.getpass("Administrator password (15 to 1024 characters): ")
        confirmation = getpass.getpass("Repeat password: ")
        if password != confirmation:
            raise BootstrapError("Passwords do not match")
        create_administrator(database, args.login, password)
        print("Administrator created")
        result = 0
    except (BootstrapError, ValueError):
        print("Administrator was not created; check password/login requirements and existing users")
        result = 2
    except (DatabaseNotReady, SQLAlchemyError):
        print("Administrator was not created; database or migrations are unavailable")
        result = 1
    except (EOFError, KeyboardInterrupt):
        print("Administrator creation cancelled")
        result = 2
    except Exception:
        print("Administrator was not created")
        result = 1
    finally:
        if database is not None:
            try:
                database.close()
            except Exception:
                print("Administrator command could not close database resources")
                result = 1
    return result


if __name__ == "__main__":
    raise SystemExit(main())
