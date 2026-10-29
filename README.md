# SYM Mobile Backend

The API for the Speak Your Mind mobile app, plus the admin console at `/admin`. One FastAPI service,
deployed on Render, sharing the Neon Postgres database with the web backend.

## How it shares the database with the web backend

| Schema | Owner | What we do |
|---|---|---|
| `public` | Web backend (Django migrations) | Read `organizations`, `topic_pool`. Read and write `users`, `debate_sessions`, `debate_messages`, `debate_results`, `user_debate_stats`. **Never** create, alter, drop or delete. |
| `mobile` | This service (Alembic) | Everything mobile-only: admin accounts and audit log, content (committees, agendas, countries, topics, settings), student profiles and sign-in tokens, MUN sessions, live debates, reports and blocks, AI usage, push notifications, job runs. |

Rules that keep both backends safe:

- **Migrations only touch `mobile`.** `migrations/env.py` filters every other schema out of autogenerate.
- **The web tables are a contract.** `app/web/contract.py` lists every web column we rely on. At startup
  (and on `/health`) the service compares it with the live database. In staging/production a mismatch
  stops the deploy, so Render keeps the previous version running.
- **Student passwords use Django's format** (`pbkdf2_sha256`, 1,000,000 iterations), so one account signs in
  on both the website and the app. `tests/test_passwords.py` proves it against real Django.

## What it does

- **Student accounts** (`/v1/auth`, `/v1/me`): sign-up with email codes, sign-in, password reset, profile,
  username, account deletion, device registration for notifications, the website's Skill Ladder.
- **Content** (`/v1/catalog`): committees, agendas, countries and debate topics, edited in the admin console.
- **MUN practice** (`/v1/mun`): AI opponents, transcription, speech analysis, Points of Information, feedback.
- **Debate vs AI** (`/v1/debates`): the AI opponent and analysis; results saved to the website's tables.
- **Live 1:1 debates** (`/v1/challenges`, `/v1/live`): invites, lobby, a server-run room clock, LiveKit voice,
  the AI judge (with a built-in judge on the server as fallback), private reports, report and block.
- **Admin console** (`/admin`): dashboard, content, settings, students, live debates, moderation,
  notifications and scheduled jobs, AI guidance and usage, activity log.
- **Scheduled jobs** (`python -m app.jobs`, every 5 minutes): debate reminders and the daily data clean-up.
- **Public pages**: `/privacy` and `/terms` (linked from the app and the store listings).

All AI is optional: without Gemini/Deepgram keys (or with AI switched off in the console) the app uses its
built-in coach, opponent and judge.

## Layout

```
app/
  core/        settings, database, passwords, tokens, signing, TOTP, audit log, rate limits, client IP
  ai/          Gemini and Deepgram clients, admin guidance, prompt safety, usage log
  models/      tables in the `mobile` schema
  web/         the web-table contract and access to the shared tables
  modules/     one folder per feature (auth, me, catalog, mun, debates, live, legal, admin_*)
  notify/      push notifications through Expo
  legal/       Privacy Policy and Terms pages
  jobs.py      scheduled reminders and clean-up
  main.py      app factory, request guard (size limit + security headers), serves the admin console
admin/         admin console (React + Vite + TypeScript), built into app/static/admin
migrations/    Alembic, `mobile` schema only
db/            web schema snapshot (structure only, no data) + local database init
scripts/       create_admin, load_test
docs/          DEPLOY (runbook, launch checklist), SECURITY, DATA_AND_PRIVACY
tests/
```

## Run it locally

Needs Docker, [uv](https://docs.astral.sh/uv/) and Node 22.

```bash
docker compose up -d --wait          # Postgres 17 with the web tables' structure (no data), port 5433
cp .env.example .env                 # then set SECRET_KEY to any long random string
uv sync
uv run alembic upgrade head          # creates the `mobile` schema and tables
(cd admin && npm ci && npm run build)
uv run uvicorn app.main:app --reload
```

- API docs: http://localhost:8000/docs
- Health: http://localhost:8000/health
- Admin console: http://localhost:8000/admin

For admin console development with hot reload, run `npm run dev` in `admin/` and open http://localhost:5180/admin
(API calls are proxied to port 8000).

### Create an admin

```bash
uv run python -m scripts.create_admin --email you@symedu.in --name "Your Name" --role super_admin
```

You're asked for a password (12+ characters). At first sign-in the console shows a QR code for an
authenticator app; two-step sign-in is mandatory for every admin.

Roles: `super_admin` (everything), `content_manager`, `moderator`, `analyst` (read-only).

## Checks

```bash
uv run ruff format --check . && uv run ruff check .
uv run mypy app scripts
uv run pytest -q                      # builds a throwaway `sym_test` database on the compose server
(cd admin && npm run build)           # type-checks and builds the console
```

GitHub Actions runs all of these, plus a Docker build, on every push and pull request.

## Refresh the web schema snapshot

When the web backend changes its tables, refresh `db/web_schema.sql` (structure only, never data) and rerun
the tests. A connection string for the web database goes in a local env file that is never committed:

```bash
docker run --rm --env-file <file with PGHOST, PGDATABASE, PGUSER, PGPASSWORD, PGSSLMODE=require> \
  postgres:17-alpine pg_dump --schema-only --no-owner --no-privileges --schema=public > db/web_schema.sql
```

If a contract test fails afterwards, update `app/web/contract.py` and the code that uses the changed columns.

## Load test

`uv run python scripts/load_test.py --log <server log> --students 200` simulates students in live rooms
(polling every second) and browsing the app, then prints latency percentiles. Results and sizing are in
[docs/DEPLOY.md](docs/DEPLOY.md#capacity).

## Deploy (Render)

See **[docs/DEPLOY.md](docs/DEPLOY.md)** for the full runbook and launch checklist. In short: `render.yaml` is a
Render blueprint with the API (Docker, Virginia, next to Neon, health check `/health`, `alembic upgrade head`
before each deploy) and a cron job for `python -m app.jobs`, sharing one environment group.

Set `DATABASE_URL` in Render to Neon's **pooled** connection string with the asyncpg driver:
`postgresql+asyncpg://<user>:<password>@<host>-pooler.<region>.aws.neon.tech/<db>?ssl=require`
(`DATABASE_POOLED=true` is already set, which turns off asyncpg's statement cache as PgBouncer requires).

Then create the first admin from the Render shell: `python -m scripts.create_admin …`.

### Database login (to do before production)

The service should connect with its own least-privilege login (`sym_mobile`): full control of the
`mobile` schema, read access to the web tables above, insert/update on the five shared tables, no delete and
no DDL on `public`. The SQL for this still has to be added and approved; until then use a dedicated
Neon login for staging only.
