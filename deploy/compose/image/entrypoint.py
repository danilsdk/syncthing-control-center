"""Explicit core composition; no automatic administrator or background worker."""

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request
import xml.etree.ElementTree as ET
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from uuid import UUID

SECRET_ROOT = Path("/run/secrets")
FOLDERS = ("scc-a4-shared", "scc-a4-control")


def secret(name, root=SECRET_ROOT):
    path = root / name
    if not path.is_file() or path.stat().st_size > 16384:
        raise ValueError("Secret is missing or exceeds its size bound")
    value = path.read_text(encoding="utf-8").strip()
    if not value or "\x00" in value:
        raise ValueError("Invalid secret")
    return value


def ownership(root=SECRET_ROOT):
    value = json.loads(secret("owner", root))
    if (
        set(value) != {"schema", "project", "owner"}
        or value["schema"] != 1
        or value["project"] != os.environ.get("SCC_PROJECT")
        or not re.fullmatch(r"scc-a4-(?:dev|test-[a-z0-9]{8,20})", value["project"])
    ):
        raise ValueError("Invalid private owner marker")
    UUID(value["owner"])
    return value


def settings(root=SECRET_ROOT):
    from app.config import Settings
    from sqlalchemy import URL

    ownership(root)
    connections = json.loads(secret("connections", root))
    if set(connections) != {"demo-a", "demo-b"}:
        raise ValueError("Invalid demo allowlist")
    for letter in "ab":
        if connections[f"demo-{letter}"]["base_url"] != f"http://syncthing-{letter}:8384":
            raise ValueError("Invalid demo endpoint")
    return Settings(
        database_url=URL.create(
            "postgresql+psycopg",
            username="scc",
            password=secret("database_password", root),
            host="postgres",
            port=5432,
            database="scc",
        ).render_as_string(hide_password=False),
        session_secret=secret("session_secret", root),
        connections=connections,
    )


def text(parent, name, value):
    element = parent.find(name)
    if element is None:
        element = ET.SubElement(parent, name)
    element.text = value


def native(*args):
    result = subprocess.run(
        ["/usr/local/bin/syncthing", *map(str, args)],
        env={key: value for key, value in os.environ.items() if not key.startswith("ST")},
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode:
        raise RuntimeError("Demo CLI operation failed")
    return result.stdout.strip()


def init_demo():
    owner = ownership()
    configuration = settings()
    nodes = []
    for letter in "ab":
        volume = Path(f"/node-{letter}")
        stamp = volume / ".scc-owner.json"
        expected = {**owner, "node": letter}
        if stamp.exists():
            if json.loads(stamp.read_text(encoding="utf-8")) != expected:
                raise ValueError("Demo volume belongs to another owner")
            if not (volume / "config/config.xml").is_file():
                raise ValueError("Incomplete demo volume retained for inspection")
        else:
            if any(volume.iterdir()):
                raise ValueError("Refusing an unmarked nonempty demo volume")
            # The marker is written before generation; incomplete initialization fails closed.
            stamp.write_text(json.dumps(expected), encoding="utf-8")
            native("generate", "--home", volume / "config", "--no-port-probing")
        device = native("device-id", "--home", volume / "config")
        if not re.fullmatch(r"[A-Z2-7]{7}(?:-[A-Z2-7]{7}){7}", device):
            raise ValueError("Invalid generated demo identity")
        nodes.append((letter, volume, device))
    for letter, volume, device in nodes:
        config_file = volume / "config/config.xml"
        configured = volume / ".scc-configured"
        key = configuration.connections[f"demo-{letter}"].api_key.get_secret_value()
        document = ET.parse(config_file)
        root = document.getroot()
        if configured.exists():
            if root.findtext("gui/apikey") != key:
                raise ValueError("Demo key differs; explicit rotation is required")
            continue  # Preserve operator config and state on subsequent up/restart.
        gui, options = root.find("gui"), root.find("options")
        text(gui, "address", "0.0.0.0:8384")
        text(gui, "apikey", key)
        gui.set("tls", "false")
        text(gui, "metricsWithoutAuth", "false")
        for name, value in {
            "globalAnnounceEnabled": "false",
            "localAnnounceEnabled": "false",
            "relaysEnabled": "false",
            "natEnabled": "false",
            "urAccepted": "-1",
            "crashReportingEnabled": "false",
            "autoUpgradeIntervalH": "0",
            "upgradeToPreReleases": "false",
        }.items():
            text(options, name, value)
        for element in options.findall("listenAddress"):
            options.remove(element)
        ET.SubElement(options, "listenAddress").text = "tcp://0.0.0.0:22000"
        for element in root.findall("device"):
            if element.get("id") != device:
                root.remove(element)
        peer_letter, _, peer_id = next(node for node in nodes if node[0] != letter)
        peer = ET.SubElement(root, "device", {"id": peer_id, "name": f"demo-{peer_letter}"})
        ET.SubElement(peer, "address").text = f"tcp://syncthing-{peer_letter}:22000"
        for element in root.findall("folder"):
            root.remove(element)
        for identifier in FOLDERS:
            folder = deepcopy(root.find("defaults/folder"))
            folder.attrib.update(
                id=identifier,
                label=identifier,
                path=f"/var/syncthing/data/{identifier}",
                type="sendreceive",
                rescanIntervalS="7200",
                fsWatcherEnabled="false",
            )
            for element in folder.findall("device"):
                folder.remove(element)
            for _, _, participant in nodes:
                ET.SubElement(folder, "device", {"id": participant, "introducedBy": ""})
            text(folder, "paused", "false")
            minimum = folder.find("minDiskFree")
            minimum.text, minimum.attrib["unit"] = "0", "B"
            (volume / "data" / identifier).mkdir(parents=True, exist_ok=True)
            root.append(folder)
        document.write(config_file, encoding="utf-8", xml_declaration=True)
        configured.write_text("1\n", encoding="ascii")
        # Only a verified, newly owned named volume is traversed.
        for path in [volume, *volume.rglob("*")]:
            os.chown(path, 1000, 1000, follow_symlinks=False)
    print("Two owned demo volumes initialized; existing identities/config are preserved")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "mode", choices=("api", "migrate", "admin-create", "collect-once", "init-demo", "probe")
    )
    parser.add_argument("--login", default="admin")
    args = parser.parse_args()
    if args.mode == "probe":
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open("http://127.0.0.1:8000/health/ready", timeout=3) as response:
            if response.status != 200:
                raise RuntimeError("API is not ready")
        return
    if args.mode == "init-demo":
        init_demo()
        return
    configuration = settings()
    if args.mode == "api":
        import uvicorn
        from app.api.events import router
        from app.main import create_app

        uvicorn.run(
            create_app(configuration, routers=(router,)),
            host="0.0.0.0",
            port=8000,
            workers=1,
            access_log=False,
            proxy_headers=False,
        )
        return
    # Existing CLI/SQL operations use an explicit environment derived from the same secrets.
    os.environ["SCC_DATABASE_URL"] = configuration.database_url.get_secret_value()
    if args.mode == "migrate":
        for action in [("upgrade", "head"), ("check",)]:
            result = subprocess.run(
                [sys.executable, "-m", "alembic", "-c", "/opt/scc/alembic.ini", *action],
                capture_output=True,
                timeout=60,
            )
            if result.returncode:
                raise RuntimeError("Migration operation failed")
        print("Migrations ready; no schema drift")
    elif args.mode == "admin-create":
        os.execv(
            sys.executable,
            [sys.executable, "-m", "app.registry", "admin-create", "--login", args.login],
        )
    else:
        from app.collection import CollectionService
        from app.contracts.interfaces import SystemClock
        from app.db import Database

        database = Database(configuration)
        try:
            database.check_ready()
            results = CollectionService(configuration, database, SystemClock()).collect_once(
                force=True
            )
            print(json.dumps([asdict(result) for result in results], default=str))
            if any(
                r.events.outcome == "error" or r.observations.outcome == "error" for r in results
            ):
                raise RuntimeError("Collection pass failed")
        finally:
            database.close()


if __name__ == "__main__":
    try:
        main()
    except (Exception, KeyboardInterrupt):
        print(
            "Core operation failed or cancelled; private values were not printed", file=sys.stderr
        )
        raise SystemExit(1) from None
