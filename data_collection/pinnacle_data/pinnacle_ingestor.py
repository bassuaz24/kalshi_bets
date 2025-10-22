#!/usr/bin/env python3
"""
pinnacle_ingestor.py

- Auth: Basic (PINNACLE_USERNAME / PINNACLE_PASSWORD)
- Endpoints: /v1/sports, /v1/leagues, /v1/fixtures, /v1/odds
- Polls selected sports/leagues, merges fixtures + odds into unified rows
- Writes to a single CSV (daily or global)

Notes:
- Uses oddsFormat=DECIMAL
- Markets: moneyline, spreads, totals
- Robust to missing fields and minor schema shifts
"""

import os
import sys
import time
import json
import base64
import requests
import pandas as pd
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

# =========================
# 🔐 Config — EDIT THESE
# =========================

# Auth mode: "basic" (official Pinnacle) or "api_key" (data partner/gateway)
AUTH_MODE = os.getenv("PINNACLE_AUTH_MODE", "basic")  # "basic" or "api_key"

# Basic auth (official Pinnacle)
PINNACLE_USERNAME = os.getenv("PINNACLE_USERNAME", "YOUR_PINNACLE_USERNAME")
PINNACLE_PASSWORD = os.getenv("PINNACLE_PASSWORD", "YOUR_PINNACLE_PASSWORD")

# API-key auth (partners/gateways)
PINNACLE_API_KEY = os.getenv("c6e3dfb23amsh78557ee88b89066p195550jsnc830a159ea59", "")  # put your key here or via env
API_KEY_HEADER_NAME = os.getenv("PINNACLE_API_KEY_HEADER", "X-API-Key")  # e.g., "X-API-Key" or "Authorization"
API_KEY_PREFIX = os.getenv("PINNACLE_API_KEY_PREFIX", "")  # e.g., "Bearer " if needed


# Odds format & markets
ODDS_FORMAT = "DECIMAL"
MARKETS = "moneyline,spreads,totals"

# Scope: choose which sports/leagues to log.
# You can let the script auto-discover by SPORT_NAME_KEYWORDS (e.g., "basketball"),
# and optionally include only leagues whose names contain any of LEAGUE_NAME_KEYWORDS.
SPORT_NAME_KEYWORDS = ["basketball", "football"]     # ["basketball"] for NBA, NCAAB; ["football"] for NFL, NCAAF
LEAGUE_NAME_KEYWORDS = ["NBA", "NCAAF", "College Football"]  # optional; [] = include all leagues in sport

# Polling / run behavior
POLL_INTERVAL = 30         # seconds between cycles
RUN_DURATION_MINUTES = 10  # set None to run indefinitely
USE_DAILY_FILE = True      # True = one CSV per day; False = one global CSV

OUTPUT_DIR = "pinnacle_data_logs"
os.makedirs(OUTPUT_DIR, exist_ok=True)
GLOBAL_CSV = os.path.join(OUTPUT_DIR, "pinnacle_odds.csv")

# Backoff
RETRY_STATUS = {429, 500, 502, 503, 504}
RETRY_SLEEP = 2.0
MAX_RETRIES = 3


# =========================
# 🔧 Helpers
# =========================

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

def to_float(x) -> Optional[float]:
    try:
        return float(x)
    except Exception:
        return None

def http_get(session: requests.Session, path: str, params: Optional[dict] = None) -> Optional[dict]:
    url = f"{API_BASE}/{API_VERSION}/{path.lstrip('/')}"
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = session.get(url, headers=HEADERS, params=params, timeout=20)
            if r.status_code == 200:
                return r.json()
            if r.status_code in RETRY_STATUS:
                time.sleep(RETRY_SLEEP * attempt)
                continue
            # Non-retryable error
            print(f"❌ GET {url} HTTP {r.status_code}: {r.text[:200]}", file=sys.stderr)
            return None
        except requests.RequestException as e:
            print(f"⚠️ GET {url} error: {e}", file=sys.stderr)
            time.sleep(RETRY_SLEEP * attempt)
    return None

def build_session(username: str, password: str) -> requests.Session:
    if not username or not password or username == "YOUR_PINNACLE_USERNAME":
        print("❌ Set PINNACLE_USERNAME and PINNACLE_PASSWORD (env vars or edit the script).", file=sys.stderr)
        sys.exit(1)
    s = requests.Session()
    # Basic auth header
    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    s.headers.update({"Authorization": f"Basic {token}"})
    return s


# =========================
# 📚 Discovery
# =========================

def fetch_sports(session: requests.Session) -> List[dict]:
    data = http_get(session, "sports")
    return data.get("sports", []) if isinstance(data, dict) else []

def choose_sports(sports: List[dict], sport_keywords: List[str]) -> List[dict]:
    if not sport_keywords:
        return sports
    out = []
    for s in sports:
        name = (s.get("name") or "").lower()
        if any(kw.lower() in name for kw in sport_keywords):
            out.append(s)
    return out

def fetch_leagues(session: requests.Session, sport_id: int) -> List[dict]:
    data = http_get(session, f"leagues", params={"sportId": sport_id})
    return data.get("leagues", []) if isinstance(data, dict) else []

def choose_leagues(leagues: List[dict], league_keywords: List[str]) -> List[dict]:
    if not league_keywords:
        return leagues
    out = []
    for lg in leagues:
        name = (lg.get("name") or "")
        if any(kw.lower() in name.lower() for kw in league_keywords):
            out.append(lg)
    return out


# =========================
# 🧩 Fixtures & Odds
# =========================

def fetch_fixtures(session: requests.Session, sport_id: int, league_ids: List[int], is_live: Optional[bool] = None) -> dict:
    """
    Returns a dict keyed by eventId with fixture info for quick joins.
    """
    params = {
        "sportId": sport_id,
        "leagueIds": ",".join(str(x) for x in league_ids) if league_ids else None,
        "isLive": str(is_live).lower() if is_live is not None else None
    }
    # Clean None
    params = {k: v for k, v in params.items() if v is not None}
    data = http_get(session, "fixtures", params=params)
    fixtures_by_id = {}
    if not isinstance(data, dict):
        return fixtures_by_id

    # Expected: { "sportId": ..., "last": ..., "leagues": [ { leagueId, events: [...] } ] }
    leagues = data.get("leagues") or []
    for lg in leagues:
        league_id = lg.get("id") or lg.get("leagueId")
        league_name = lg.get("name")
        for ev in lg.get("events", []):
            ev_id = ev.get("id")
            if ev_id is None:
                continue
            fixtures_by_id[ev_id] = {
                "league_id": league_id,
                "league_name": league_name,
                "event_id": ev_id,
                "starts": ev.get("starts"),  # ISO datetime
                "home": ev.get("home"),
                "away": ev.get("away"),
                "rotNum": ev.get("rotNum"),
                "state": ev.get("state"),
                "liveStatus": ev.get("liveStatus"),
            }
    return fixtures_by_id

def fetch_odds(session: requests.Session, sport_id: int, league_ids: List[int], is_live: Optional[bool] = None) -> dict:
    """
    Returns a dict keyed by eventId with odds payloads.
    """
    params = {
        "sportId": sport_id,
        "leagueIds": ",".join(str(x) for x in league_ids) if league_ids else None,
        "oddsFormat": ODDS_FORMAT,
        "isLive": str(is_live).lower() if is_live is not None else None,
        "markets": MARKETS
    }
    params = {k: v for k, v in params.items() if v is not None}
    data = http_get(session, "odds", params=params)
    odds_by_id = {}
    if not isinstance(data, dict):
        return odds_by_id

    # Expected: { "leagues": [ { id:..., events: [ { id, periods:[ { moneyline, spreads, totals } ] } ] } ] }
    leagues = data.get("leagues") or []
    for lg in leagues:
        for ev in lg.get("events", []):
            ev_id = ev.get("id")
            if ev_id is None:
                continue
            odds_by_id[ev_id] = ev
    return odds_by_id


# =========================
# 🔄 Merge & Normalize
# =========================

def extract_period(ev_odds: dict, want_period_number: int = 0) -> Optional[dict]:
    """
    Pinnacle odds can have multiple periods; periodNumber=0 is "full game".
    """
    periods = ev_odds.get("periods") if isinstance(ev_odds, dict) else None
    if not isinstance(periods, list):
        return None
    for p in periods:
        if p.get("number") == want_period_number:
            return p
    # fallback to first period if 0 is absent
    return periods[0] if periods else None

def merge_fixture_odds_row(
    sport_name: str,
    league: dict,
    fixture: dict,
    ev_odds: dict
) -> dict:
    """
    Produce a single tidy row for one event.
    """
    ts = now_iso()
    league_id = league.get("id") or league.get("leagueId")
    league_name = league.get("name")

    event_id = fixture.get("event_id")
    home = fixture.get("home")
    away = fixture.get("away")
    starts = fixture.get("starts")
    is_live = (fixture.get("liveStatus") == 1 or (fixture.get("state") or "").lower() == "live")

    # Default empty markets
    moneyline_home = moneyline_away = None
    spread_home_points = spread_home_price = None
    spread_away_points = spread_away_price = None
    total_points = total_over_price = total_under_price = None

    if ev_odds:
        period = extract_period(ev_odds, want_period_number=0) or {}

        # Moneyline
        ml = period.get("moneyline") or {}
        moneyline_home = to_float(ml.get("home"))
        moneyline_away = to_float(ml.get("away"))

        # Spreads (can be array)
        spreads = period.get("spreads") or []
        if isinstance(spreads, list) and spreads:
            # Choose the first line (market maker may list alternatives)
            sp = spreads[0]
            spread_home_points = to_float(sp.get("hdp") or sp.get("points"))
            # prices may be strings/decimals
            spread_home_price = to_float((sp.get("home") or {}).get("price") if isinstance(sp.get("home"), dict) else sp.get("home"))
            spread_away_price = to_float((sp.get("away") or {}).get("price") if isinstance(sp.get("away"), dict) else sp.get("away"))
            # some variants use "altLineId"; we ignore for now

        # Totals (can be array)
        totals = period.get("totals") or []
        if isinstance(totals, list) and totals:
            tot = totals[0]
            total_points = to_float(tot.get("points"))
            total_over_price = to_float((tot.get("over") or {}).get("price") if isinstance(tot.get("over"), dict) else tot.get("over"))
            total_under_price = to_float((tot.get("under") or {}).get("price") if isinstance(tot.get("under"), dict) else tot.get("under"))

    row = {
        "timestamp": ts,
        "sport": sport_name,
        "league_id": league_id,
        "league_name": league_name,
        "event_id": event_id,
        "event_start": starts,
        "is_live": is_live,
        "home_team": home,
        "away_team": away,

        # Moneyline (decimal odds)
        "moneyline_home": moneyline_home,
        "moneyline_away": moneyline_away,

        # Spread (use home/away points as Pinnacle defines)
        "spread_home_points": spread_home_points,
        "spread_home_price": spread_home_price,
        "spread_away_points": spread_away_points,
        "spread_away_price": spread_away_price,

        # Totals
        "total_points": total_points,
        "total_over_price": total_over_price,
        "total_under_price": total_under_price,
    }
    return row


# =========================
# 💾 Writer
# =========================

def write_rows(rows: List[dict]):
    if not rows:
        return
    if USE_DAILY_FILE:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        path = os.path.join(OUTPUT_DIR, f"pinnacle_odds_{date_str}.csv")
    else:
        path = GLOBAL_CSV
    df = pd.DataFrame(rows)
    header = not os.path.exists(path)
    df.to_csv(path, index=False, mode="a", header=header)


# =========================
# ▶️ Main loop
# =========================

def main():
    session = build_session(PINNACLE_USERNAME, PINNACLE_PASSWORD)

    # 1) Discover sports
    sports = fetch_sports(session)
    if not sports:
        print("❌ Could not fetch sports; check credentials or API availability.", file=sys.stderr)
        sys.exit(1)

    chosen_sports = choose_sports(sports, SPORT_NAME_KEYWORDS)
    if not chosen_sports:
        print("❌ No sports matched SPORT_NAME_KEYWORDS. Adjust config.", file=sys.stderr)
        sys.exit(1)

    # Cache leagues per sport
    scope = []  # list of tuples (sport_id, sport_name, [league objects])
    for s in chosen_sports:
        sport_id = s.get("id")
        sport_name = s.get("name")
        if not sport_id:
            continue
        leagues = fetch_leagues(session, sport_id)
        leagues = choose_leagues(leagues, LEAGUE_NAME_KEYWORDS)
        if leagues:
            scope.append((sport_id, sport_name, leagues))

    if not scope:
        print("❌ No leagues matched LEAGUE_NAME_KEYWORDS within chosen sports. Adjust config.", file=sys.stderr)
        sys.exit(1)

    print("✅ Monitoring scope:")
    for sport_id, sport_name, leagues in scope:
        names = ", ".join([lg.get("name", f"id={lg.get('id')}") for lg in leagues[:6]])
        more = "" if len(leagues) <= 6 else f" (+{len(leagues)-6} more)"
        print(f"- {sport_name} ({sport_id}): {names}{more}")

    # 2) Poll loop
    end_time = None
    if RUN_DURATION_MINUTES:
        end_time = datetime.now(timezone.utc) + timedelta(minutes=RUN_DURATION_MINUTES)

    cycle = 0
    try:
        while True:
            if end_time and datetime.now(timezone.utc) >= end_time:
                print("⏹️ Reached run duration. Exiting.")
                break

            cycle += 1
            ts = now_iso()
            print(f"\n⏱️ Cycle {cycle} @ {ts}")

            batch = []

            for sport_id, sport_name, leagues in scope:
                league_ids = [int(lg.get("id") or lg.get("leagueId")) for lg in leagues if (lg.get("id") or lg.get("leagueId")) is not None]
                if not league_ids:
                    continue

                # Fetch fixtures & odds and merge
                fixtures_by_id = fetch_fixtures(session, sport_id, league_ids, is_live=None)
                odds_by_id = fetch_odds(session, sport_id, league_ids, is_live=None)

                # Group leagues for row context (league name/id)
                leagues_by_id = {int(lg.get("id") or lg.get("leagueId")): lg for lg in leagues if (lg.get("id") or lg.get("leagueId")) is not None}

                # Group odds by league if needed (we have the league from cached scope)
                # Build rows
                for ev_id, fx in fixtures_by_id.items():
                    lg_id = int(fx.get("league_id")) if fx.get("league_id") is not None else None
                    league_obj = leagues_by_id.get(lg_id, {"id": lg_id, "name": None})

                    ev_odds = odds_by_id.get(ev_id, {})
                    row = merge_fixture_odds_row(sport_name, league_obj, fx, ev_odds)
                    batch.append(row)

                # Also capture any odds with no fixtures (edge cases)
                for ev_id, ev_od in odds_by_id.items():
                    if ev_id in fixtures_by_id:
                        continue
                    # Build a minimal fixture stub from odds
                    starts = None
                    home = ev_od.get("home") or None
                    away = ev_od.get("away") or None
                    stub_fx = {
                        "league_id": None,
                        "league_name": None,
                        "event_id": ev_id,
                        "starts": starts,
                        "home": home,
                        "away": away,
                        "state": None,
                        "liveStatus": None,
                    }
                    lg_stub = {"id": None, "name": None}
                    row = merge_fixture_odds_row(sport_name, lg_stub, stub_fx, ev_od)
                    batch.append(row)

                # polite pacing between sports
                time.sleep(0.2)

            if batch:
                write_rows(batch)
                print(f"💾 Wrote {len(batch)} rows to CSV.")
            else:
                print("↩️ Nothing to write this cycle.")

            time.sleep(POLL_INTERVAL)

    except KeyboardInterrupt:
        print("🛑 Stopped by user.")
        return


if __name__ == "__main__":
    main()
