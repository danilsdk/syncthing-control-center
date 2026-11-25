from collections import Counter, defaultdict
from datetime import date, timedelta
from typing import Literal
from uuid import UUID

from sqlalchemy import Date, case, cast, func, literal, select, union_all
from sqlalchemy.orm import Session

from app.contracts.dto import Analytics, AnalyticsDay, AnalyticsPeriod, ApiMetrics, CommandMetrics
from app.contracts.types import ExecutionStatus
from app.models import CommandExecution, StatusSnapshot


class AnalyticsFilter(AnalyticsPeriod):
    node_id: UUID | None = None
    source: Literal["rule", "manual"] | None = None


def _query(filters: AnalyticsFilter):
    completed_at = CommandExecution.completed_at
    created_at = CommandExecution.created_at
    observed_at = StatusSnapshot.observed_at
    command_day = cast(func.timezone("UTC", completed_at), Date)
    pending_day = cast(func.timezone("UTC", created_at), Date)
    probe_day = cast(func.timezone("UTC", observed_at), Date)
    command_filters = []
    probe_filters = []
    if filters.node_id is not None:
        command_filters.append(CommandExecution.node_id == filters.node_id)
        probe_filters.append(StatusSnapshot.node_id == filters.node_id)
    if filters.source is not None:
        command_filters.append(CommandExecution.source == filters.source)

    completed = (
        select(command_day, CommandExecution.status, func.count())
        .where(completed_at >= filters.from_, completed_at < filters.to, *command_filters)
        .group_by(command_day, CommandExecution.status)
    )
    pending = (
        select(pending_day, literal(ExecutionStatus.PENDING.value), func.count())
        .where(
            CommandExecution.status == ExecutionStatus.PENDING,
            created_at >= filters.from_,
            created_at < filters.to,
            *command_filters,
        )
        .group_by(pending_day)
    )
    probes = (
        select(
            probe_day,
            case((StatusSnapshot.success, "probe_ok"), else_="probe_failed"),
            func.count(),
        )
        .where(
            StatusSnapshot.scope == "node",
            observed_at >= filters.from_,
            observed_at < filters.to,
            *probe_filters,
        )
        .group_by(probe_day, StatusSnapshot.success)
    )
    # A single statement sees one PostgreSQL snapshot, including concurrent transitions.
    return union_all(completed, pending, probes)


def _command_metrics(counts: Counter[str]) -> CommandMetrics:
    applied = counts[ExecutionStatus.APPLIED]
    failed = counts[ExecutionStatus.FAILED]
    return CommandMetrics(
        applied=applied,
        failed=failed,
        unknown=counts[ExecutionStatus.UNKNOWN],
        cancelled=counts[ExecutionStatus.CANCELLED],
        pending_created=counts[ExecutionStatus.PENDING],
        success_ratio=applied / (applied + failed) if applied + failed else None,
    )


def _api_metrics(counts: Counter[str]) -> ApiMetrics:
    successful = counts["probe_ok"]
    total = successful + counts["probe_failed"]
    return ApiMetrics(
        successful_checks=successful,
        total_checks=total,
        success_ratio=successful / total if total else None,
    )


def summarize(session: Session, filters: AnalyticsFilter) -> Analytics:
    by_day: dict[date, Counter[str]] = defaultdict(Counter)
    totals: Counter[str] = Counter()
    for day, metric, count in session.execute(_query(filters)):
        by_day[day][metric] += count
        totals[metric] += count

    first = filters.from_.date()
    last = (filters.to - timedelta(microseconds=1)).date()
    daily = []
    for offset in range((last - first).days + 1):
        day = first + timedelta(days=offset)
        counts = by_day[day]
        daily.append(
            AnalyticsDay(
                date=day, commands=_command_metrics(counts), api_checks=_api_metrics(counts)
            )
        )

    return Analytics(
        from_=filters.from_,
        to=filters.to,
        node_id=filters.node_id,
        source=filters.source,
        commands=_command_metrics(totals),
        api_checks=_api_metrics(totals),
        daily=tuple(daily),
    )
