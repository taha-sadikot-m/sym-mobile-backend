# Security

How the backend protects student accounts and data, what the October 2026 review found, and what's still open.

## How it's protected

**Student sign-in:**
- Passwords are stored in Django's format (PBKDF2, 1,000,000 iterations), shared with the website.
- Access tokens are signed JWTs (HS256, 15 minutes).
- Refresh tokens are rotated on every use and stored only as hashes. Re-using an old one signs that device
  family out.
- Email codes are hashed, expire after 15 minutes, and allow 5 attempts. Sends are capped at 5 per hour and 15
  per day per address.
- Sign-up, sign-in, codes and invites are rate-limited per address and per IP.

**Admin console:**
- Separate admin accounts with Argon2 passwords and **mandatory** authenticator codes.
- The session cookie is HttpOnly, SameSite=Strict, Secure, and limited to `/admin`.
- Every write needs the `X-SYM-Admin` header, which blocks cross-site requests.
- Accounts lock after repeated failures, and sign-in is also throttled per IP.
- Every role is checked on the server, and every change is written to the audit log.
- Students' personal details are visible only to super admins and moderators.

**Live debates:**
- The verdict is always decided **on the server**: the AI judge, or a server port of the built-in judge. A phone
  can't post a verdict.
- Each student can store only their own private report.
- Turns can only be written by their speaker, in that speaker's own slot, one per slot.
- Leaving early counts as a loss for the student who left.

**Practice debates:**
- A win counts on the website only when the server signed the AI analysis that declared it.
- Unsigned results (from the built-in analysis) are saved as practice with capped scores, never as wins.

**AI:**
- Student text is placed in prompts as quoted material. Angle brackets are replaced, so it can't close a tag.
- Model answers must match a fixed JSON shape, and scores are clamped and totals computed on the server.

**Requests:**
- A request guard rejects bodies over 1 MB before reading them (26 MB for audio uploads).
- Free-text fields have length limits.
- Security headers are set on every response, including a strict CSP on the admin console.

**Client IP:**
- Taken from the last `TRUSTED_PROXY_HOPS` entries of `X-Forwarded-For` (Render: 1) and validated as an IP, so
  clients can't spoof it.

**Database:**
- The website's tables are never altered or deleted from. A column contract stops deploys when they change.
- Account deletion anonymises the shared `users` row with UPDATE.

**Secrets:** environment variables only. `.env` is ignored by git and excluded from the Docker image.

## Review, 18 October 2026

An independent code review found the issues below; all are fixed and covered by tests (`tests/test_security.py`,
`tests/test_live_api.py`, `tests/test_debates_api.py`, `tests/test_notify_jobs.py`, `tests/test_student_auth.py`).

| Severity | Issue | Fix |
|---|---|---|
| Critical | `verify-email` issued tokens for an already-verified account without checking the code (account takeover). | Verified accounts get 409 and no tokens; the code is always checked. |
| High | A student could post their own live-debate verdict. | Verdicts are computed only on the server; the endpoint was removed; leaving is a forfeit. |
| High | A student could delete the opponent's turn by re-using its id. | Turns are replaced only by their speaker, in their own slot. |
| High | `--forwarded-allow-ips='*'` let clients spoof their IP (bypassing rate limits, forging audit IPs). | Fixed trusted-hop count, IP validation, per-IP admin throttle. |
| Medium | Any user id could be blocked, revealing names; students saw each other's schools. | Block requires a real student; schools are never shown to other students. |
| Medium | Practice wins could be forged. | Server-signed analysis required for a win; scores capped otherwise; saves rate-limited. |
| Medium | Student text could close prompt tags. | Brackets neutralised everywhere student text enters a prompt. |
| Medium | Account deletion left MUN sessions, live transcripts, reports, blocks, rate-limit keys. | Deletion now removes or anonymises all of them, including website transcript text. |
| Medium | Password reset re-activated accounts switched off on the website. | Switched-off verified accounts can't reset or receive codes. |
| Medium | No request-size limits. | 1 MB / 26 MB guard; bounded report and analysis fields. |
| Low | Code-attempt race, refresh-token race, Argon2 on the event loop, docs exposed on staging, `/health` details public, email codes logged when Resend is missing, Gemini key in URLs, MUN save race, invite-code guessing, analyst access to student PII. | All fixed. |

## Still open (decisions or setup, not code)

- **Age and parental consent** (DPDP Act):
  - sign-up accepts age 8+ and has no verified parental consent;
  - the product decision was to add consent later;
  - get legal advice before a public launch.
- **Database role:** the least-privilege `sym_mobile` role still has to be created (see the README).
- **Rotate exposed credentials:**
  - Neon owner password in the web repository's `.env.render`;
  - `VITE_NEON_DATABASE_URL` and `VITE_GEMINI_API_KEY` in the web frontend bundle.
- **Access tokens after sign-out:** they stay valid for up to 15 minutes after sign-out or a password change.
  Refresh tokens are revoked immediately, and a paused account is blocked on its next request.
- **Model output to minors:** AI-written report text isn't screened by a separate moderation model. Students can
  report anything inappropriate, and AI can be switched off in the admin console.
