# Runtime Requirements

This document lists what is needed to run the bot locally.

## Python
- Python 3.9+ (the repo has been run with Anaconda).
- Install dependencies from `requirements.txt`.

## Environment Variables (.env)
Create a `.env` file in the repo root with at least:
- `KALSHI_API_KEY_ID` — Kalshi key ID.
- `KALSHI_ACCESS_KEY_ID` — Kalshi access key ID.
- `KALSHI_KEY_ID` — Kalshi key ID (if your setup uses this name).
- `KALSHI_PRIVATE_KEY_PATH` — path to your `private_key.pem` (optional if the code defaults to repo root).
- `API_BET_API` — Odds API key (BetsAPI). If missing, odds feed returns empty.

If you use email snapshots, also add:
- `EMAIL_HOST` — SMTP host.
- `EMAIL_PORT` — SMTP port (e.g., 587).
- `EMAIL_HOST_USER` — SMTP username.
- `EMAIL_HOST_PASSWORD` — SMTP password or app-specific password.
- `EMAIL_FROM` — sender email address.
- `EMAIL_TO` — recipient list (comma-separated).

## Kalshi Private Key
- `private_key.pem` in the repo root (or point to it with `KALSHI_PRIVATE_KEY_PATH`).
- Keep this file private and never commit it.

## Files Created at Runtime
These are created/updated in the repo root:
- `positions.json` — open positions state.
- `first_detection_times.json` — event detection tracking.
- `event_locks.json` — event lock tracking.
- `stop_lossed_events.json` — stop-loss cooldown tracking.
- `event_7pct_exited.json` — 7% exit tracking.

## Running
- Start the bot: `python app/engine.py`
- API server starts automatically if enabled and binds to `0.0.0.0:8000`.

## Common Setup Notes
- If you see `token_authentication_failure`, check Kalshi key IDs and private key path.
- If you see missing odds, confirm `API_BET_API` is set and valid.
