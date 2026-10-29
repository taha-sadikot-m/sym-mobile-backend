"""Load test: many students polling live rooms at once, plus normal app traffic.

The hottest path is the live room: both phones ask for /v1/live/{id}/state about once a second. This
simulates `--students` clients (half of them in live rooms, polling every second; the rest browsing the
catalogue, their profile and challenges every few seconds) for `--seconds`, then prints latency
percentiles and errors per endpoint.

Local use (EMAIL_DELIVERY=log, so sign-up codes are read from the server's log file):
    uv run python scripts/load_test.py --base http://localhost:8000 --log path/to/server.log --students 200
Never point this at production.
"""

import argparse
import asyncio
import random
import re
import statistics
import time
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

PASSWORD = "Motion-Carried-26"


def _code(log: Path, email: str, mark: int) -> str:
    text = log.read_text(encoding="utf-8", errors="replace")[mark:]
    index = text.rfind(email)
    found = re.findall(r"\b(\d{6})\b", text[index:]) if index >= 0 else []
    if not found:
        raise SystemExit(f"No code for {email} in {log}")
    return str(found[0])


async def student(client: httpx.AsyncClient, log: Path, email: str) -> tuple[dict[str, str], int]:
    mark = len(log.read_text(encoding="utf-8", errors="replace"))
    response = await client.post(
        "/v1/auth/sign-up", json={"full_name": "Load Tester", "email": email, "password": PASSWORD}
    )
    response.raise_for_status()
    await asyncio.sleep(0.5)
    tokens = (
        await client.post("/v1/auth/verify-email", json={"email": email, "code": _code(log, email, mark)})
    ).json()
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    me = (await client.get("/v1/me", headers=headers)).json()
    return headers, me["id"]


async def live_room(client: httpx.AsyncClient, log: Path, stamp: int) -> tuple[str, list[dict[str, str]]]:
    (a, _), (b, b_id) = await asyncio.gather(
        student(client, log, f"load-a-{stamp}@example.org"),
        student(client, log, f"load-b-{stamp}@example.org"),
    )
    made = (
        await client.post(
            "/v1/challenges",
            headers=a,
            json={
                "topic": {
                    "id": "load",
                    "title": "Load testing is worth it",
                    "category": "custom",
                    "context": "",
                },
                "format": "quick",
                "side_preference": "for",
                "scheduled_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                "opponent_id": b_id,
            },
        )
    ).json()
    await client.post(f"/v1/challenges/{made['id']}/accept", headers=b)
    await client.post(f"/v1/challenges/{made['id']}/ready", headers=a)
    await client.post(f"/v1/challenges/{made['id']}/ready", headers=b)
    return made["id"], [a, b]


async def run(args: argparse.Namespace) -> None:
    log = Path(args.log)
    timings: dict[str, list[float]] = defaultdict(list)
    errors: dict[str, int] = defaultdict(int)
    limits = httpx.Limits(max_connections=args.students + 20, max_keepalive_connections=args.students + 20)
    async with httpx.AsyncClient(base_url=args.base, timeout=30, limits=limits) as client:
        room, (a, b) = await live_room(client, log, int(time.time()))
        print(f"Live room {room} ready; starting {args.students} clients for {args.seconds}s")
        deadline = time.monotonic() + args.seconds

        async def hit(name: str, path: str, headers: dict[str, str]) -> None:
            started = time.perf_counter()
            try:
                response = await client.get(path, headers=headers)
                if response.status_code >= 400:
                    errors[f"{name} {response.status_code}"] += 1
            except httpx.HTTPError as error:
                errors[f"{name} {type(error).__name__}"] += 1
            timings[name].append((time.perf_counter() - started) * 1000)

        async def in_room(headers: dict[str, str]) -> None:
            await asyncio.sleep(random.random())
            while time.monotonic() < deadline:
                await hit("live state", f"/v1/live/{room}/state", headers)
                await asyncio.sleep(1)

        async def browsing(headers: dict[str, str]) -> None:
            await asyncio.sleep(random.random() * 3)
            while time.monotonic() < deadline:
                name, path = random.choice(
                    [
                        ("catalog", "/v1/catalog"),
                        ("me", "/v1/me"),
                        ("challenges", "/v1/challenges"),
                        ("ladder", "/v1/me/ladder"),
                    ]
                )
                await hit(name, path, headers)
                await asyncio.sleep(3)

        # Half the students sit in live rooms polling every second; the other half browse the app.
        pollers = [in_room(a if index % 2 else b) for index in range(args.students // 2)]
        browsers = [browsing(a if index % 2 else b) for index in range(args.students - len(pollers))]
        clients = pollers + browsers
        started = time.monotonic()
        await asyncio.gather(*clients)
        elapsed = time.monotonic() - started

    total = sum(len(values) for values in timings.values())
    print(
        f"\n{total} requests in {elapsed:.0f}s = {total / elapsed:.0f} req/s, {sum(errors.values())} errors"
    )
    # If this is close to the run time, the load generator (not the server) is the limit: run several.
    print(f"load generator CPU: {time.process_time():.0f}s")
    print(f"{'endpoint':<12} {'count':>7} {'p50 ms':>8} {'p95 ms':>8} {'p99 ms':>8} {'max ms':>8}")
    for name, values in sorted(timings.items()):
        ordered = sorted(values)
        p95, p99 = (ordered[min(len(ordered) - 1, int(q * len(ordered)))] for q in (0.95, 0.99))
        median = statistics.median(ordered)
        print(f"{name:<12} {len(ordered):>7} {median:>8.0f} {p95:>8.0f} {p99:>8.0f} {ordered[-1]:>8.0f}")
    for name, count in sorted(errors.items()):
        print(f"  error {name}: {count}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--base", default="http://localhost:8000")
    parser.add_argument(
        "--log", required=True, help="the local server's log file (sign-up codes are printed there)"
    )
    parser.add_argument("--students", type=int, default=200)
    parser.add_argument("--seconds", type=int, default=60)
    args = parser.parse_args()
    if "onrender.com" in args.base or "symedu" in args.base:  # never production
        raise SystemExit("Refusing to load-test a deployed server.")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
