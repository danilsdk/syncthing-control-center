"""Fetch the pinned release and extract only its verified executable."""

from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import platform
import stat
import tarfile
import time
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

HERE = Path(__file__).resolve().parent
RUNTIME = HERE / "runtime"
MAX_ARCHIVE_BYTES = 80 * 1024 * 1024
MAX_BINARY_BYTES = 60 * 1024 * 1024
MAX_DOWNLOAD_SECONDS = 300


class BenchError(Exception):
    """Only fixed, safe operational messages are exposed to the console."""


def runtime_path(runtime: Path, path: Path) -> Path:
    """Check the lexical boundary before inspecting any filesystem redirects.

    Do not resolve the expected boundary: a link must not redefine what the
    bench owns. Check ancestors from the filesystem root so lstat never walks
    through an already accepted symlink/junction. Missing paths are permitted
    for subsequent creation; other inspection failures are refused.
    """
    runtime, path = Path(runtime), Path(path)
    if ".." in runtime.parts or ".." in path.parts:
        raise BenchError("Bench path is outside its private runtime")
    runtime, path = runtime.absolute(), path.absolute()
    if not path.is_relative_to(runtime):
        raise BenchError("Bench path is outside its private runtime")
    for component in reversed((path, *path.parents)):
        try:
            status = component.lstat()
        except FileNotFoundError:
            break
        except OSError:
            raise BenchError("Cannot verify the private runtime path") from None
        if stat.S_ISLNK(status.st_mode) or getattr(status, "st_file_attributes", 0) & 0x400:
            raise BenchError("Private runtime paths must not traverse links or reparse points")
    return path


def release() -> dict:
    return json.loads((HERE / "release.json").read_text(encoding="utf-8"))


def platform_name() -> str:
    system = platform.system()
    if system == "Darwin":
        if platform.machine().lower() != "arm64":
            raise BenchError("macOS requires native ARM64 Python; Intel/Rosetta is unsupported")
        if macos_translated():
            raise BenchError("Rosetta is unsupported; run native ARM64 Python")
        return "macos-arm64"
    if platform.machine().lower() not in {"amd64", "x86_64"}:
        raise BenchError("This bench currently pins only amd64 release archives")
    if system == "Windows":
        return "windows-amd64"
    if system == "Linux":
        return "linux-amd64"
    raise BenchError("No release archive is pinned for this operating system")


def macos_translated() -> bool:
    """Apple's sysctl probe; an unavailable observation must not claim native execution."""
    try:
        library = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
        probe = library.sysctlbyname
        probe.argtypes = (
            ctypes.c_char_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t),
            ctypes.c_void_p, ctypes.c_size_t,
        )
        probe.restype = ctypes.c_int
        value = ctypes.c_int()
        size = ctypes.c_size_t(ctypes.sizeof(value))
        ctypes.set_errno(0)
        result = probe(b"sysctl.proc_translated", ctypes.byref(value), ctypes.byref(size), None, 0)
    except (OSError, AttributeError):
        raise BenchError("Cannot verify native macOS execution") from None
    if result == -1 and ctypes.get_errno() == errno.ENOENT:
        return False  # Documented native-process result when this sysctl is absent.
    if result != 0 or size.value != ctypes.sizeof(value) or value.value not in (0, 1):
        raise BenchError("Cannot verify native macOS execution")
    return bool(value.value)


def verify_archive(path: Path, expected: str) -> None:
    if path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise BenchError("Release archive exceeds the allowed size")
    with path.open("rb") as source:
        actual = hashlib.file_digest(source, "sha256").hexdigest()
    if actual != expected:
        raise BenchError("Release SHA-256 mismatch; executable was not extracted or started")


def extract_binary(archive: Path, *, archive_format: str, executable: str) -> bytes:
    if executable not in {"syncthing", "syncthing.exe"}:
        raise BenchError("Unrecognized release executable")
    if archive_format == "zip":
        with zipfile.ZipFile(archive) as source:
            entries = [e for e in source.infolist() if release_member(e.filename, executable)]
            if (
                len(entries) != 1
                or entries[0].is_dir()
                or stat.S_IFMT(entries[0].external_attr >> 16) not in (0, stat.S_IFREG)
                or entries[0].file_size > MAX_BINARY_BYTES
            ):
                raise BenchError("Release must contain exactly one bounded executable")
            with source.open(entries[0]) as stream:
                return bounded_binary(stream)
    if archive_format != "tar.gz":
        raise BenchError("Unrecognized release archive format")
    with tarfile.open(archive, "r:gz") as source:
        entries = [e for e in source.getmembers() if release_member(e.name, executable)]
        if len(entries) != 1 or not entries[0].isfile() or entries[0].size > MAX_BINARY_BYTES:
            raise BenchError("Release must contain exactly one bounded executable")
        stream = source.extractfile(entries[0])
        if stream is None:
            raise BenchError("Release executable cannot be read")
        with stream:
            return bounded_binary(stream)


def bounded_binary(stream) -> bytes:
    content = stream.read(MAX_BINARY_BYTES + 1)
    if not content or len(content) > MAX_BINARY_BYTES:
        raise BenchError("Release executable is empty or exceeds its size limit")
    return content


def release_member(name: str, executable: str) -> bool:
    # Official Unix bundles also contain etc/*/syncthing service scripts.
    # Only the binary at the release root is eligible, never nested files.
    path = PurePosixPath(name)
    return (
        not path.is_absolute()
        and "\\" not in name
        and ".." not in path.parts
        and len(path.parts) in (1, 2)
        and path.name == executable
    )


def prepare() -> Path:
    metadata = release()
    target = platform_name()
    asset = metadata["assets"][target]
    cache = runtime_path(RUNTIME, RUNTIME / "downloads")
    archive = runtime_path(RUNTIME, cache / asset["name"])
    temporary = runtime_path(RUNTIME, archive.with_suffix(archive.suffix + ".part"))
    binary = runtime_path(
        RUNTIME,
        RUNTIME
        / "bin"
        / metadata["version"]
        / target
        / asset["executable"],
    )
    cache.mkdir(parents=True, exist_ok=True)
    if not archive.exists():
        request = urllib.request.Request(asset["url"], headers={"User-Agent": "SCC-A1-bench"})
        started = time.monotonic()
        try:
            with (
                urllib.request.urlopen(request, timeout=30) as response,
                runtime_path(RUNTIME, temporary).open("wb") as out,
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
            runtime_path(RUNTIME, temporary).replace(runtime_path(RUNTIME, archive))
        except Exception:
            runtime_path(RUNTIME, temporary).unlink(missing_ok=True)
            raise
    verify_archive(archive, asset["sha256"])
    content = extract_binary(
        archive, archive_format=asset["archive_format"], executable=asset["executable"]
    )
    runtime_path(RUNTIME, binary.parent).mkdir(parents=True, exist_ok=True)
    if not binary.exists() or binary.read_bytes() != content:
        runtime_path(RUNTIME, binary).write_bytes(content)
    runtime_path(RUNTIME, binary).chmod(binary.stat().st_mode | 0o111)
    return binary


if __name__ == "__main__":
    try:
        print(prepare())
    except Exception as exc:
        print(str(exc) if isinstance(exc, BenchError) else "Release preparation failed")
        raise SystemExit(1) from None
