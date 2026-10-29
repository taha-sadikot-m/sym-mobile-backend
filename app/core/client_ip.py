"""The real client address behind Render's proxy, safe to use for rate limits and audit logs.

Each proxy in front of us appends the address it received the request from to `X-Forwarded-For`, so only
the last `TRUSTED_PROXY_HOPS` entries were written by infrastructure we trust; anything to their left was
sent by the client and can be forged. Render adds exactly one hop (TRUSTED_PROXY_HOPS=1); locally there is
no proxy (0) and the socket address is used. A value that isn't an IP address is ignored.
"""

import ipaddress

from fastapi import Request

from app.core.config import get_settings


def _valid(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return str(ipaddress.ip_address(value.strip()))
    except ValueError:
        return None


def client_ip(request: Request) -> str | None:
    peer = request.client.host if request.client else None
    hops = get_settings().trusted_proxy_hops
    if hops <= 0:
        return _valid(peer)
    chain = [part.strip() for part in request.headers.get("x-forwarded-for", "").split(",") if part.strip()]
    if len(chain) < hops:
        return _valid(peer)
    return _valid(chain[-hops]) or _valid(peer)
