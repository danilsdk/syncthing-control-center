"""Public projections keep source configuration, runtime and rule targets separate."""

from collections.abc import Callable
from datetime import datetime

from app.contracts import dto
from app.contracts.errors import AppError
from app.models import Folder, ManagedNode, SyncRule

DesiredState = Callable[[SyncRule, datetime], bool]


def stale(timestamp: datetime | None, now: datetime, threshold: int) -> bool:
    return timestamp is None or timestamp > now or (now - timestamp).total_seconds() > threshold


def node_dto(node: ManagedNode, now: datetime, threshold: int) -> dto.Node:
    return dto.Node(
        id=node.id,
        display_name=node.display_name,
        enabled=node.enabled,
        syncthing_device_id=node.syncthing_device_id,
        api_status=node.api_status,
        last_checked_at=node.last_checked_at,
        last_seen_at=node.last_seen_at,
        is_stale=(
            not node.enabled or node.api_status != "ok" or stale(node.last_seen_at, now, threshold)
        ),
        created_at=node.created_at,
        updated_at=node.updated_at,
    )


def folder_dto(
    folder: Folder,
    node: ManagedNode,
    rule: SyncRule | None,
    provider: DesiredState | None,
    now: datetime,
    threshold: int,
) -> dto.Folder:
    desired = None
    if rule is not None:
        try:
            if provider is None:
                raise ValueError
            desired = provider(rule, now)
            if type(desired) is not bool:
                raise ValueError
        except Exception:
            raise AppError(503, "service_unavailable", "Desired state is unavailable") from None
    return dto.Folder(
        id=folder.id,
        node_id=folder.node_id,
        syncthing_folder_id=folder.syncthing_folder_id,
        label=folder.label,
        present=folder.present,
        actual_paused=folder.actual_paused,
        config_observed_at=folder.config_observed_at,
        runtime_state=folder.runtime_state,
        runtime_observed_at=folder.runtime_observed_at,
        need_bytes=folder.need_bytes,
        pull_errors=folder.pull_errors,
        desired_paused=desired,
        active_rule_id=rule.id if rule is not None else None,
        is_stale=(
            not node.enabled
            or node.api_status != "ok"
            or not folder.present
            or stale(folder.config_observed_at, now, threshold)
        ),
    )
