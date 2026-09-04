"""Local fake core-banking UI for the computer-use assignment.

Run:
    pip install fastapi uvicorn jinja2
    uvicorn demo_target.app:app --host 127.0.0.1 --port 3000
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

ROOT = Path(__file__).resolve().parent
MEMBER_ID_RE = re.compile(r"^M-\d{4}$")

MEMBERS: dict[str, dict[str, str]] = {
    "M-1001": {"name": "Ada Member", "savings_balance": "410.22"},
    "M-1002": {"name": "Ben Member", "savings_balance": "80.00"},
}

# Member exists but operator lacks permission to view.
RESTRICTED_MEMBERS = {"M-1003"}


@dataclass
class PageFlags:
    dialog: bool = False
    slow: bool = False
    expired: bool = False
    confirm: bool = False


app = FastAPI(title="Demo Core — Member Lookup", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
templates = Jinja2Templates(directory=str(ROOT / "templates"))


def _page_flags(request: Request) -> PageFlags:
    qp = request.query_params
    return PageFlags(
        dialog=qp.get("dialog") == "1",
        slow=qp.get("slow") == "1",
        expired=qp.get("expired") == "1",
        confirm=qp.get("confirm") == "1",
    )


def _strip_query_flag(request: Request, flag: str) -> str:
    parsed = urlparse(str(request.url))
    params = parse_qs(parsed.query, keep_blank_values=True)
    params.pop(flag, None)
    flat = {k: v[0] for k, v in params.items() if v}
    query = urlencode(flat)
    return urlunparse(parsed._replace(query=query))


async def _maybe_slow(slow: bool) -> None:
    if slow:
        await asyncio.sleep(2.5)


def _render(
    request: Request,
    template: str,
    *,
    status_code: int = 200,
    **context: object,
) -> HTMLResponse:
    flags = _page_flags(request)
    return templates.TemplateResponse(
        request,
        template,
        {
            "dialog": flags.dialog,
            "confirm": flags.confirm,
            "dismiss_url": _strip_query_flag(request, "dialog"),
            "confirm_url": _strip_query_flag(request, "confirm"),
            **context,
        },
        status_code=status_code,
    )


@app.get("/", response_class=HTMLResponse)
async def search_page(request: Request, error: str | None = None) -> HTMLResponse:
    flags = _page_flags(request)
    if flags.expired:
        await _maybe_slow(flags.slow)
        return _render(request, "session_expired.html", status_code=401)
    await _maybe_slow(flags.slow)
    return _render(request, "search.html", error=error, member_id="")


@app.post("/search")
async def search_submit(request: Request, member_id: str = Form("")) -> RedirectResponse:
    flags = _page_flags(request)
    await _maybe_slow(flags.slow)

    member_id = member_id.strip().upper()
    extra = request.url.query
    suffix = f"?{extra}" if extra else ""

    if not MEMBER_ID_RE.match(member_id):
        loc = f"/?error=invalid{('&' + extra) if extra else ''}"
        return RedirectResponse(loc, status_code=303)

    return RedirectResponse(f"/members/{member_id}{suffix}", status_code=303)


@app.get("/members/{member_id}", response_class=HTMLResponse)
async def member_detail(request: Request, member_id: str) -> HTMLResponse:
    flags = _page_flags(request)
    await _maybe_slow(flags.slow)

    member_id = member_id.strip().upper()
    if not MEMBER_ID_RE.match(member_id):
        return _render(
            request,
            "search.html",
            error="invalid",
            member_id=member_id,
            status_code=400,
        )

    if member_id in RESTRICTED_MEMBERS:
        return _render(
            request,
            "permission_denied.html",
            member_id=member_id,
            status_code=403,
        )

    member = MEMBERS.get(member_id)
    if member is None:
        return _render(
            request,
            "not_found.html",
            member_id=member_id,
            status_code=404,
        )

    return _render(
        request,
        "detail.html",
        member_id=member_id,
        name=member["name"],
        savings_balance=member["savings_balance"],
    )


@app.get("/members/{member_id}/close", response_class=HTMLResponse)
async def close_account(request: Request, member_id: str) -> HTMLResponse:
    """Irreversible-looking action. Automation allowlist should block this."""
    flags = _page_flags(request)
    await _maybe_slow(flags.slow)
    member_id = member_id.strip().upper()
    member = MEMBERS.get(member_id)
    if member is None:
        return _render(request, "not_found.html", member_id=member_id, status_code=404)
    return _render(
        request,
        "close_confirm.html",
        member_id=member_id,
        name=member["name"],
    )


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
