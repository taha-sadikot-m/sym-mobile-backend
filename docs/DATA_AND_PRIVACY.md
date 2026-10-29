# Data and privacy

What the mobile backend stores, where it goes, how long it's kept, and what account deletion does. The public
Privacy Policy (`/privacy`, source `app/legal/privacy.html`) must stay consistent with this page.

## What is stored

| Data | Where | Notes |
|---|---|---|
| Name, email, password hash, verification status | `public.users` (shared with the website) | Django's table; we insert and update, never delete. |
| Username, birth year, school, city, country, MUN experience, app status | `mobile.profiles` | All optional. School is never shown to other students. |
| Sign-in sessions (refresh tokens, hashed) and email codes (hashed) | `mobile.refresh_tokens`, `mobile.email_codes` | Cleaned up automatically (below). |
| MUN practice sessions (scores, report) | `mobile.mun_sessions` | Text only. |
| Debates vs AI | `public.debate_sessions`, `debate_messages`, `debate_results`, `user_debate_stats` | Shown in the student's website history. |
| Live debates (challenge, room events, turn transcripts, verdict, private reports) | `mobile.challenges`, `live_events`, `live_results` | Also saved to the website's debate tables as PVP. |
| Reports and blocks | `mobile.user_reports`, `mobile.blocks` | For moderation. |
| Push tokens | `mobile.push_tokens` | Removed on sign-out, when notifications are switched off, or when Expo says the device is gone. |
| Notification log | `mobile.notifications` | Title, body, delivery counts. |
| AI usage (no transcripts: tokens, seconds, time, success) | `mobile.ai_calls` | For cost and reliability. |
| Admin audit log | `mobile.audit_log` | Who changed what, from which IP. |

**Audio is never stored.** Recordings go from the phone to Deepgram for transcription and are discarded. Live voice
goes through LiveKit in real time without recording.

## Processors

| Processor | Purpose | Data |
|---|---|---|
| Google (Gemini API) | Feedback, Points of Information, opponent, judge | Transcripts, topic, country |
| Deepgram | Speech to text | Audio (not retained by us) |
| LiveKit | Live debate voice | Real-time audio |
| Neon | Database | Everything above |
| Render | Hosting | Everything in transit |
| Resend | Email codes | Email address, name |
| Expo | Push delivery | Push token, notification text |

## Retention

The clean-up job (`app/jobs.py`, daily) enforces:

| Data | Kept for |
|---|---|
| Email codes | 1 day after expiry |
| Refresh tokens | 30 days after expiry or revocation |
| Rate-limit counters | 2 days |
| Admin sessions | 30 days after expiry or revocation |
| Notification log | 90 days |
| AI usage log | 400 days |
| Unanswered challenges | marked expired after 48 hours |
| Accounts and practice history | until the student deletes the account |

## Account deletion

Triggered by the student (Profile → Delete account, password required) or by a super admin. `erase_student` in
`app/modules/me/router.py`:

- **Deletes:**
  - profile, refresh tokens, email codes, push tokens;
  - MUN sessions;
  - the student's turn transcripts in live debates and their private live reports;
  - blocks (both directions) and reports the student filed;
  - rate-limit counters keyed by their email.
- **Cancels** open challenges.
- **Anonymises:**
  - the shared `users` row (name "Deleted user", random email, unusable password, inactive);
  - the student's messages in the website's debate transcripts (`[deleted]`);
  - the user id on AI usage rows.
- **Keeps:**
  - reports *about* the student, for the moderation record;
  - debate results opponents took part in, which now show "Deleted user".
- **Records** the deletion in `mobile.account_deletions`.

## Children

Most students are under 18. Today:
- the app asks under-18s to involve a parent or guardian;
- there is no verified parental consent (a product decision to add later);
- schools aren't shown to other students;
- reporting, blocking and moderation are in place.

Under India's DPDP Act, verifiable parental consent for under-18s is expected. Plan it before a public launch.
