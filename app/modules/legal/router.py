"""Public Privacy Policy and Terms pages (the app stores require a public URL; the app links here)."""

import html
from functools import cache
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app.core.config import get_settings

router = APIRouter(tags=["legal"])

PAGES = Path(__file__).resolve().parents[2] / "legal"
UPDATED = "18 October 2026"


@cache
def _page(name: str, title: str, support: str) -> str:
    body = (PAGES / f"{name}.html").read_text(encoding="utf-8")
    contact = (
        f'<a href="mailto:{html.escape(support)}">{html.escape(support)}</a>'
        if support
        else "the contact details on the SYM website"
    )
    body = body.replace("{{updated}}", UPDATED).replace("{{support}}", contact)
    frame = (PAGES / "frame.html").read_text(encoding="utf-8")
    return frame.replace("[[title]]", title).replace("[[body]]", body)


@router.get("/privacy", response_class=HTMLResponse, include_in_schema=False)
async def privacy() -> HTMLResponse:
    return HTMLResponse(_page("privacy", "Privacy Policy", get_settings().support_email))


@router.get("/terms", response_class=HTMLResponse, include_in_schema=False)
async def terms() -> HTMLResponse:
    return HTMLResponse(_page("terms", "Terms of Use", get_settings().support_email))
