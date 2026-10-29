"""A0 observations and safe folder commands for one server-resolved source."""

import json
import re
from contextlib import contextmanager
from threading import RLock
from urllib.parse import quote, urlencode

from pydantic import TypeAdapter, ValidationError

from app.contracts.dto import EventPayload
from app.contracts.interfaces import (
    AdapterError,
    Clock,
    EventBatch,
    FolderConfig,
    FolderRuntime,
    NodeConnection,
    RawTimestamp,
    SourceCheckpoint,
    SourceEvent,
    StreamAnchor,
    SystemClock,
    SystemObservation,
    source_session_id,
)
from app.contracts.types import NonEmpty, SyncthingFolderId
from app.syncthing_adapter.transport import HttpTransport, Transport

FOLDER_ID = TypeAdapter(SyncthingFolderId)


def _no_duplicates(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate JSON property")
        value[key] = item
    return value


def _reject_constant(_value):
    raise ValueError("Non-JSON numeric constant")


class HttpSyncthingAdapter:
    def __init__(
        self,
        connection: NodeConnection,
        *,
        expected_device_id: str | None = None,
        clock: Clock | None = None,
        transport: Transport | None = None,
        checkpoint: SourceCheckpoint | None = None,
    ):
        if expected_device_id is not None and (
            not isinstance(expected_device_id, str) or not expected_device_id.strip()
        ):
            raise ValueError("Invalid expected Syncthing identity")
        if checkpoint is not None and (
            not isinstance(checkpoint, SourceCheckpoint) or checkpoint.node_id != connection.node_id
        ):
            raise ValueError("Source checkpoint must belong to the requested node")
        self._connection = connection
        self._expected_id = expected_device_id
        self._clock = clock or SystemClock()
        self._transport = transport if transport is not None else HttpTransport(connection)
        self._lock = RLock()
        self._closed = False
        self._checkpoint = checkpoint
        self._uptime = None
        self._requires_rebase = checkpoint is None or checkpoint.anchor is None

    def close(self) -> None:
        self._closed = True
        self._transport.close()

    @contextmanager
    def _operation(self):
        if not self._lock.acquire(timeout=5):
            raise AdapterError("timeout")
        try:
            if self._closed:
                raise AdapterError("unreachable")
            yield
        finally:
            self._lock.release()

    def _identity_header(self, response, *, write=False):
        if response.device_id is not None and response.device_id != self._expected_id:
            raise AdapterError("identity_mismatch", operation="write" if write else "read")

    def _json(self, path, *, timeout=5):
        response = self._transport.request("GET", path, timeout=timeout)
        if self._expected_id is not None:
            self._identity_header(response)
        if response.content_type.split(";", 1)[0].strip().lower() != "application/json":
            raise AdapterError("invalid_response")
        try:
            value = json.loads(
                response.body.decode("utf-8"),
                object_pairs_hook=_no_duplicates,
                parse_constant=_reject_constant,
            )
        except (ValueError, UnicodeError, RecursionError):
            raise AdapterError("invalid_response") from None
        if (
            path == "/rest/system/status"
            and response.device_id is not None
            and (isinstance(value, dict) and value.get("myID") != response.device_id)
        ):
            raise AdapterError("identity_mismatch")
        return value

    def _status(self):
        value = self._json("/rest/system/status")
        try:
            device_id = TypeAdapter(NonEmpty).validate_python(value["myID"])
            start_time = TypeAdapter(RawTimestamp).validate_python(value["startTime"])
            uptime = value.get("uptime")
            if uptime is not None and (type(uptime) is not int or uptime < 0):
                raise ValueError
        except (KeyError, TypeError, ValueError, ValidationError):
            raise AdapterError("invalid_response") from None
        if self._expected_id is not None and device_id != self._expected_id:
            raise AdapterError("identity_mismatch")
        self._expected_id = device_id
        return device_id, start_time, uptime

    def _system(self):
        before = self._status()
        window = self._events(self._json("/rest/events?since=0&timeout=0"))
        after = self._status()
        if before[:2] != after[:2]:
            raise AdapterError("session_changed")
        if not window:
            raise AdapterError("session_changed")  # No evidence to establish a source epoch yet.
        saved = self._checkpoint
        same_start = saved is not None and saved.start_time_raw == after[1]
        matching = None
        if same_start and saved.anchor is not None:
            matching = next((event for event in window if event.id == saved.anchor.id), None)
        continued = matching is not None and StreamAnchor.from_event(matching) == saved.anchor
        uptime_regressed = (
            before[2] is not None and after[2] is not None and after[2] < before[2]
        ) or (
            same_start
            and self._uptime is not None
            and after[2] is not None
            and after[2] < self._uptime
        )
        if continued and not uptime_regressed:
            epoch = saved.source_session_id
        else:
            epoch = source_session_id(
                self._connection.node_id,
                after[1],
                StreamAnchor.from_event(window[0]),
                previous_session_id=saved.source_session_id if saved is not None else None,
            )
            self._requires_rebase = True
        observation = SystemObservation(
            device_id=after[0],
            start_time_raw=after[1],
            source_session_id=epoch,
            observed_at=self._clock.now_utc(),
            stream_anchor=StreamAnchor.from_event(window[-1]),
        )
        self._checkpoint = SourceCheckpoint.from_observation(self._connection.node_id, observation)
        self._uptime = after[2]
        return observation

    @staticmethod
    def _folder_id(value):
        try:
            result = FOLDER_ID.validate_python(value)
            if result in {".", ".."} or any(ord(char) < 32 for char in result):
                raise ValueError
            return result
        except (ValidationError, ValueError):
            raise AdapterError("invalid_response") from None

    def _folder(self, value):
        try:
            folder_id = self._folder_id(value["id"])
            label = value["label"]
            if not isinstance(label, str):
                raise ValueError
            # A0 exposes an 80-character display name, not the full source config.
            label = (label if label.strip() else folder_id)[:80]
            return FolderConfig(
                syncthing_folder_id=folder_id,
                label=label,
                paused=value["paused"],
                observed_at=self._clock.now_utc(),
            )
        except (KeyError, TypeError, ValueError, ValidationError):
            raise AdapterError("invalid_response") from None

    def read_system(self) -> SystemObservation:
        with self._operation():
            return self._system()

    def list_folders(self) -> list[FolderConfig]:
        with self._operation():
            self._system()
            value = self._json("/rest/config/folders")
            if not isinstance(value, list):
                raise AdapterError("invalid_response")
            folders = [self._folder(item) for item in value]
            if len({item.syncthing_folder_id for item in folders}) != len(folders):
                raise AdapterError("invalid_response")
            return folders

    def read_folder(self, folder_id: str) -> FolderConfig:
        with self._operation():
            folder_id = self._folder_id(folder_id)
            self._system()
            result = self._folder(self._json("/rest/config/folders/" + quote(folder_id, safe="")))
            if result.syncthing_folder_id != folder_id:
                raise AdapterError("invalid_response")
            return result

    def read_runtime(self, folder_id: str) -> FolderRuntime:
        with self._operation():
            folder_id = self._folder_id(folder_id)
            self._system()
            value = self._json("/rest/db/status?" + urlencode({"folder": folder_id}))
            try:
                return FolderRuntime(
                    state=value["state"],
                    need_bytes=value.get("needBytes"),
                    pull_errors=value.get("pullErrors"),
                    observed_at=self._clock.now_utc(),
                )
            except (KeyError, TypeError, ValueError, ValidationError):
                raise AdapterError("invalid_response") from None

    def restart_required(self) -> bool:
        with self._operation():
            self._system()
            value = self._json("/rest/config/restart-required")
            if not isinstance(value, dict) or type(value.get("requiresRestart")) is not bool:
                raise AdapterError("invalid_response")
            return value["requiresRestart"]

    def set_paused(self, folder_id: str, paused: bool) -> None:
        try:
            with self._operation():
                folder_id = self._folder_id(folder_id)
                if type(paused) is not bool:
                    raise AdapterError("invalid_response")
                self._system()  # No PATCH is sent if the source identity is wrong.
                try:
                    response = self._transport.request(
                        "PATCH",
                        "/rest/config/folders/" + quote(folder_id, safe=""),
                        body=json.dumps({"paused": paused}, separators=(",", ":")).encode(),
                        timeout=5,
                    )
                    self._identity_header(response, write=True)
                except AdapterError as error:
                    raise AdapterError(
                        error.code,
                        operation="write",
                        write_outcome=error.write_outcome or "unknown",
                    ) from None
        except AdapterError as error:
            if error.write_outcome is not None:
                raise
            raise AdapterError(error.code, operation="write", write_outcome="not_applied") from None

    @staticmethod
    def _events(value):
        if not isinstance(value, list):
            raise AdapterError("invalid_response")
        result = []
        try:
            for item in value:
                data = item.get("data")
                data = data if isinstance(data, dict) else {}
                folder = data.get("folder")
                state = data.get("to") if item["type"] == "StateChanged" else None
                if not isinstance(state, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", state):
                    state = None
                result.append(
                    SourceEvent(
                        id=item["id"],
                        global_id=item.get("globalID"),
                        type=item["type"],
                        time_raw=item["time"],
                        syncthing_folder_id=folder,
                        payload_summary=EventPayload(state=state)
                        if state is not None
                        else EventPayload(),
                    )
                )
        except (KeyError, AttributeError, TypeError, ValueError, ValidationError):
            raise AdapterError("invalid_response") from None
        ids = [event.id for event in result]
        if any(left >= right for left, right in zip(ids, ids[1:], strict=False)):
            raise AdapterError("invalid_response")
        return result

    def read_events(self, since: int, timeout_seconds: int = 15) -> EventBatch:
        if type(since) is not int or not 0 <= since <= 2**63 - 1:
            raise ValueError("Invalid source event cursor")
        if type(timeout_seconds) is not int or not 0 <= timeout_seconds <= 15:
            raise ValueError("Event timeout must be an integer from 0 through 15")
        with self._operation():
            before = self._system()
            if (self._requires_rebase and since != 0) or since > before.stream_anchor.id:
                raise AdapterError("session_changed")
            events = self._events(
                self._json(
                    "/rest/events?" + urlencode({"since": since, "timeout": timeout_seconds}),
                    timeout=timeout_seconds + 5,
                )
            )
            after = self._system()
            if after.source_session_id != before.source_session_id:
                raise AdapterError("session_changed")
            if any(event.id <= since for event in events):
                raise AdapterError("invalid_response")
            if events and after.stream_anchor.id < events[-1].id:
                raise AdapterError("session_changed")
            try:
                result = EventBatch(
                    source_session_id=after.source_session_id,
                    events=events,
                    observed_at=self._clock.now_utc(),
                    source_checkpoint=self._checkpoint,
                )
            except ValidationError:
                raise AdapterError("invalid_response") from None
            self._requires_rebase = False
            return result
