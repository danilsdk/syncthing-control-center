from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.contracts.errors import AppError
from app.models import Folder, ManagedNode, SyncRule
from app.registry.projections import folder_dto, node_dto

NOW = datetime(2026, 10, 25, 12, tzinfo=UTC)


def records():
    node = ManagedNode(
        id=uuid4(),
        display_name="Demo",
        connection_ref="node-a",
        enabled=True,
        api_status="ok",
        last_checked_at=NOW,
        last_seen_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    )
    folder = Folder(
        id=uuid4(),
        node_id=node.id,
        syncthing_folder_id="shared",
        label="Shared",
        present=True,
        actual_paused=False,
        config_observed_at=NOW,
        runtime_state=None,
        runtime_observed_at=None,
        need_bytes=None,
        pull_errors=None,
    )
    return node, folder


@pytest.mark.parametrize(
    "reason", ["disabled", "unreachable", "unknown", "old", "future", "missing"]
)
def test_node_staleness_never_fabricates_success(reason):
    node, _ = records()
    if reason == "disabled":
        node.enabled = False
    elif reason in {"unreachable", "unknown"}:
        node.api_status = reason
    elif reason == "old":
        node.last_seen_at = NOW - timedelta(seconds=61)
    elif reason == "future":
        node.last_seen_at = NOW + timedelta(seconds=1)
        node.last_checked_at = node.last_seen_at
    else:
        node.last_seen_at = None
    assert node_dto(node, NOW, 60).is_stale


def test_stale_boundary_and_unknown_runtime_are_independent_of_paused_config():
    node, folder = records()
    folder.actual_paused = True
    folder.config_observed_at = NOW - timedelta(seconds=60)
    current = folder_dto(folder, node, None, None, NOW, 60)
    assert not current.is_stale and current.actual_paused is True
    assert current.runtime_state is None and current.need_bytes is None
    folder.present = False
    assert folder_dto(folder, node, None, None, NOW, 60).is_stale


@pytest.mark.parametrize("provider", [None, lambda *_: None, lambda *_: 1])
def test_active_rule_requires_authoritative_bool_target(provider):
    node, folder = records()
    rule = SyncRule(id=uuid4(), folder_id=folder.id, enabled=True)
    with pytest.raises(AppError) as error:
        folder_dto(folder, node, rule, provider, NOW, 60)
    assert error.value.code == "service_unavailable"


def test_target_is_not_inferred_from_actual_paused():
    node, folder = records()
    rule = SyncRule(id=uuid4(), folder_id=folder.id, enabled=True)
    result = folder_dto(folder, node, rule, lambda *_: True, NOW, 60)
    assert result.desired_paused is True and result.actual_paused is False
    assert result.active_rule_id == rule.id
