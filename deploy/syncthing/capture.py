"""Allowlisted, explicitly reduced fixtures from the disposable A1 bench."""

from __future__ import annotations

import json
import re
from pathlib import Path

from prepare import BenchError


def selected(value: dict, names: tuple[str, ...]) -> dict:
    return {name: value[name] for name in names if name in value}


def folder(value: dict, node: str) -> dict:
    result = selected(
        value,
        (
            "id",
            "label",
            "type",
            "paused",
            "rescanIntervalS",
            "fsWatcherEnabled",
            "fsWatcherDelayS",
            "filesystemType",
        ),
    )
    result["path"] = f"/DEMO/{node}/{value['id']}"
    result["devices"] = [selected(device, ("deviceID",)) for device in value["devices"]]
    return result


def sanitize(kind: str, value, node: str = "node-a"):
    if kind == "system-status":
        return selected(value, ("myID", "startTime", "uptime"))
    if kind == "system-version":
        return selected(value, ("version", "os", "arch"))
    if kind == "folders":
        return [folder(item, node) for item in value]
    if kind == "folder-config":
        return folder(value, node)
    if kind == "folder-runtime":
        return selected(
            value,
            (
                "state",
                "needBytes",
                "pullErrors",
                "stateChanged",
                "globalBytes",
                "localBytes",
                "sequence",
            ),
        )
    if kind == "restart-required":
        return selected(value, ("requiresRestart",))
    if kind == "events":
        result = []
        for event in value:
            item = selected(event, ("id", "globalID", "time", "type"))
            data = event.get("data")
            item["data"] = {}
            if isinstance(data, dict):
                if data.get("folder") in {"scc-a1-shared", "scc-a1-control"}:
                    item["data"]["folder"] = data["folder"]
                if event["type"] == "StateChanged":
                    for key in ("state", "from", "to"):
                        text = data.get(key)
                        if isinstance(text, str) and re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", text):
                            item["data"][key] = text
            result.append(item)
        return result
    raise BenchError("Fixture type is not allowlisted")


def assert_public(value, forbidden: list[str]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    # Check both decoded JSON and escaped representations (not only backslashes).
    plain = json.dumps(value, ensure_ascii=False)
    for secret in forbidden:
        if secret and (secret in encoded or json.dumps(secret, ensure_ascii=False)[1:-1] in plain):
            raise BenchError("Fixture publication refused: private data is still present")
    return encoded


def publish(directory: Path, fixtures: dict, manifest: dict, forbidden: list[str]) -> None:
    # Validate everything before writing any fixture.
    content = {name: assert_public(value, forbidden) for name, value in fixtures.items()}
    content["manifest.json"] = assert_public(manifest, forbidden)
    if any(Path(name).name != name or not name.endswith(".json") for name in content):
        raise BenchError("Unsafe fixture filename")
    directory.mkdir(parents=True, exist_ok=True)
    for name, text in content.items():
        (directory / name).write_text(text, encoding="utf-8")
