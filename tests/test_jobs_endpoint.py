import httpx
import pytest

from app.core.config import get_settings
from app.main import app


async def call(headers: dict[str, str] | None = None) -> httpx.Response:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        return await client.post("/v1/jobs/run", headers=headers or {})


async def test_jobs_endpoint_is_off_without_a_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "cron_secret", "")
    assert (await call({"Authorization": "Bearer anything"})).status_code == 404


async def test_jobs_endpoint_needs_the_secret_and_runs_the_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "cron_secret", "s" * 40)
    assert (await call()).status_code == 401
    assert (await call({"Authorization": "Bearer wrong"})).status_code == 401
    response = await call({"Authorization": f"Bearer {'s' * 40}"})
    assert response.status_code == 200
    runs = {run["name"]: run for run in response.json()}
    assert runs["reminders"]["ok"] is True
    assert "reminded" in runs["reminders"]["details"]
