"""Fetch the pinned release and extract only its verified executable."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import tarfile
import time
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNTIME = HERE / "runtime"
MAX_ARCHIVE_BYTES = 80 * 1024 * 1024
MAX_BINARY_BYTES = 60 * 1024 * 1024
MAX_DOWNLOAD_SECONDS = 300


class BenchError(Exception):
    """Only fixed, safe operational messages are exposed to the console."""


def release() -> dict:
    return json.loads((HERE / "release.json").read_text(encoding="utf-8"))


def platform_name() -> str:
    if platform.machine().lower() not in {"amd64", "x86_64"}:
        raise BenchError("This bench currently pins only amd64 release archives")
    if os.name == "nt":
        return "windows-amd64"
    if platform.system() == "Linux":
        return "linux-amd64"
    raise BenchError("No release archive is pinned for this operating system")


def verify_archive(path: Path, expected: str) -> None:
    if path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise BenchError("Release archive exceeds the allowed size")
    with path.open("rb") as source:
        actual = hashlib.file_digest(source, "sha256").hexdigest()
    if actual != expected:
        raise BenchError("Release SHA-256 mismatch; executable was not extracted or started")


def extract_binary(archive: Path, windows: bool) -> bytes:
    name = "syncthing.exe" if windows else "syncthing"
    if windows:
        with zipfile.ZipFile(archive) as source:
            entries = [entry for entry in source.infolist() if Path(entry.filename).name == name]
            if len(entries) != 1 or entries[0].file_size > MAX_BINARY_BYTES:
                raise BenchError("Release must contain exactly one bounded executable")
            return source.read(entries[0])
    with tarfile.open(archive, "r:gz") as source:
        entries = [entry for entry in source.getmembers() if Path(entry.name).name == name]
        if len(entries) != 1 or not entries[0].isfile() or entries[0].size > MAX_BINARY_BYTES:
            raise BenchError("Release must contain exactly one bounded executable")
        stream = source.extractfile(entries[0])
        if stream is None:
            raise BenchError("Release executable cannot be read")
        with stream:
            return stream.read()


def prepare() -> Path:
    metadata = release()
    target = platform_name()
    asset = metadata["assets"][target]
    cache = RUNTIME / "downloads"
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / asset["name"]
    if not archive.exists():
        temporary = archive.with_suffix(archive.suffix + ".part")
        request = urllib.request.Request(asset["url"], headers={"User-Agent": "SCC-A1-bench"})
        started = time.monotonic()
        try:
            with (
                urllib.request.urlopen(request, timeout=30) as response,
                temporary.open("wb") as out,
            ):
                total = 0
                progress = 0
                while chunk := response.read(64 * 1024):
                    total += len(chunk)
                    if (
                        total > MAX_ARCHIVE_BYTES
                        or time.monotonic() - started > MAX_DOWNLOAD_SECONDS
                    ):
                        raise BenchError("Release download exceeded its size or time budget")
                    out.write(chunk)
                    if total // (1024 * 1024) > progress:
                        progress = total // (1024 * 1024)
                        print(f"Pinned release download: {progress} MiB", flush=True)
            verify_archive(temporary, asset["sha256"])
            temporary.replace(archive)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
    verify_archive(archive, asset["sha256"])
    content = extract_binary(archive, target.startswith("windows"))
    binary = (
        RUNTIME
        / "bin"
        / metadata["version"]
        / target
        / ("syncthing.exe" if target.startswith("windows") else "syncthing")
    )
    binary.parent.mkdir(parents=True, exist_ok=True)
    if not binary.exists() or binary.read_bytes() != content:
        binary.write_bytes(content)
    binary.chmod(binary.stat().st_mode | 0o111)
    return binary


if __name__ == "__main__":
    try:
        print(prepare())
    except Exception as exc:
        print(str(exc) if isinstance(exc, BenchError) else "Release preparation failed")
        raise SystemExit(1) from None
