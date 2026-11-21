"""Analytics router included under the shared administrator dependency."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.analytics.summary import AnalyticsFilter, summarize
from app.api.dependencies import get_session
from app.contracts import dto

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get(
    "/summary",
    response_model=dto.Analytics,
    operation_id="analytics_summary",
    responses={code: {"model": dto.Error} for code in (401, 422, 503)},
)
def analytics_summary(
    filters: Annotated[AnalyticsFilter, Query()],
    session: Annotated[Session, Depends(get_session)],
) -> dto.Analytics:
    return summarize(session, filters)
