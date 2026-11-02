"""Scheduled jobs over HTTP, for hosts without their own cron (Vercel's free plan runs crons once a day).

An outside scheduler (such as cron-job.org) calls `POST /v1/jobs/run` every 5 minutes with the header
`Authorization: Bearer <CRON_SECRET>`. It runs exactly what `python -m app.jobs` runs: debate reminders,
and the clean-up at most once a day. Without CRON_SECRET the endpoint doesn't exist (404).
"""

import secrets
from typing import Any

from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel

from app import jobs
from app.core.config import get_settings

router = APIRouter(tags=["jobs"])


class JobOut(BaseModel):
    name: str
    ok: bool
    details: dict[str, Any] | None
    error: str | None


@router.api_route("/v1/jobs/run", methods=["GET", "POST"])
async def run_jobs(authorization: str = Header(default="")) -> list[JobOut]:
    expected = get_settings().cron_secret
    if not expected:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    given = authorization.removeprefix("Bearer ").strip()
    if not secrets.compare_digest(given.encode(), expected.encode()):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, detail={"message": "Wrong cron secret.", "code": "unauthorized"}
        )
    runs = await jobs.run_due()
    return [JobOut(name=run.name, ok=bool(run.ok), details=run.details, error=run.error) for run in runs]
