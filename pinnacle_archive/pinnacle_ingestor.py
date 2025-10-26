#!/usr/bin/env python3
"""
pinnacle_ingestor.py — RapidAPI (Tipsters / Pinnacle Odds)

- Endpoints (fixed):
    kit/v1/sports
    kit/v1/leagues?sport_id=<id>
    kit/v1/fixtures?sport_id=&league_ids=&is_live=
    kit/v1/odds?sport_id=&league_ids=&odds_format=&markets=&is_live=
    kit/v1/markets?event_type=&sport_id=&league_ids=&is_have_odds=
- Auth: X-RapidAPI-Key, X-RapidAPI-Host
- Scope toggles: keywords & allowlists
- Uses /markets to narrow to events that have odds
- Logs one row per event snapshot (moneyline, spread, total)

Setup:
  python3 -m venv .venv && source .venv/bin/activate
  pip install requests pandas
Run:
  export RAPIDAPI_KEY="your_key"
  python pinnacle_ingestor.py
"""

import os
import sys
import time
import json
import requests
import pandas as pd
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Dict

# =========================
# 🔐 RapidAPI config — EDIT
# =========================
RAPIDAPI_KEY  = os.getenv("RAPIDAPI_KEY", "c6e3dfb23amsh78557ee88b89066p195550jsnc830a159ea59")
RAPIDAPI_HOST = os.getenv("RAPIDAPI_HOST", "pinnacle-odds.p.rapidapi.com")
BASE_URL      = f"https://{RAPIDAPI_HOST}"

HEADERS = {
    "Accept": "application/json",
    "User-Agent": "pinnacle-rapidapi-ingestor/1.2",
    "X-RapidAPI-Key": RAPIDAPI_KEY,
    "X-RapidAPI-Host": RAPIDAPI_HOST,
}

def require_key():
    if not RAPIDAPI_KEY or RAPIDAPI_KEY == "YOUR_RAPIDAPI_KEY":
        sys.exit("❌ Set RAPIDAPI_KEY (env var or edit the script).")

# =========================
# 📌 Endpoint paths & params
# =========================
ENDPOINTS = {
    "sports":   "kit/v1/sports",
    "leagues":  "kit/v1/leagues",
    "fixtures": "kit/v1/fixtures",
    "odds":     "kit/v1/odds",
    "markets":  "kit/v1/markets",
}
PARAM_KEYS = {
    "sport_id":    "sport_id",
    "league_ids":  "league_ids",
    "is_live":     "is_live",
    "odds_format": "odds_format",
    "markets":     "markets",
    "event_type":  "event_type",
    "is_have_odds":"is_have_odds",
}

# =========================
# 🎯 Scope toggles — EDIT
# =========================
SPORT_NAME_KEYWORDS  = []   # e.g., ["basketball"] for NBA/NCAAB
LEAGUE_NAME_KEYWORDS = []

ALLOWLIST_SPORT_IDS  = []   # e.g., [5] (your product's sport ids)
ALLOWLIST_LEAGUE_IDS = []   # e.g., [487] for NBA
ALLOWLIST_EVENT_IDS  = []   # restrict to these events if non-empty

# Markets query controls
EVENT_TYPE    = "prematch"  # "prematch" or "live"
IS_HAVE_ODDS  = True        # only markets currently with odds
ONLY_UPCOMING_HOURS = 72    # None to disable time window filter (fixtures)

# =========================
# ⏱️ Loop & Output — EDIT
# =========================
POLL_INTERVAL = 30                # seconds between cycles
RUN_DURATION_MINUTES = 2         # None for continuous
USE_DAILY_FILE = True
OUTPUT_DIR = "pinnacle_data_logs"
os.makedirs(OUTPUT_DIR, exist_ok=True)
GLOBAL_CSV = os.path.join(OUTPUT_DIR, "pinnacle_odds.csv")

# =========================
# 🔁 Backoff / Retry
# =========================
RETRY_STATUS = {429, 500, 502, 503, 504}
RETRY_SLEEP = 2.0
MAX_RETRIES = 3

# =========================
# 🧰 Helpers
# =========================
def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

def to_float(x) -> Optional[float]:
    try:
        return float(x)
    except Exception:
        return None

def http_get(path: str, params: Optional[dict] = None) -> Optional[dict]:
    url = f"{BASE_URL.rstrip('/')}/{path.lstrip('/')}"
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = requests.get(url, headers=HEADERS, params=params, timeout=20)
            if r.status_code == 200:
                try:
                    return r.json()
                except Exception:
                    print(f"⚠️ Non-JSON at {url}: {r.text[:200]}", file=sys.stderr)
                    return None
            if r.status_code in RETRY_STATUS:
                time.sleep(RETRY_SLEEP * attempt)
                continue
            print(f"❌ GET {url} {r.status_code}: {r.text[:200]}", file=sys.stderr)
            return None
        except requests.RequestException as e:
            print(f"⚠️ GET {url} error: {e}", file=sys.stderr)
            time.sleep(RETRY_SLEEP * attempt)
    return None

def filter_by_keywords(items: List[dict], key: str, keywords: List[str]) -> List[dict]:
    if not keywords:
        return items
    out = []
    for it in items:
        name = (it.get(key) or "")
        if any(kw.lower() in name.lower() for kw in keywords):
            out.append(it)
    return out

def within_upcoming_window(starts_iso: Optional[str], hours: Optional[int]) -> bool:
    if hours is None or not starts_iso:
        return True
    try:
        st = datetime.fromisoformat(starts_iso.replace("Z", "+00:00")).astimezone(timezone.utc)
        return st <= datetime.now(timezone.utc) + timedelta(hours=hours)
    except Exception:
        return True

# ===== Debug switch =====
DEBUG = True  # flip to False after it works

def http_get(path: str, params: Optional[dict] = None) -> Optional[dict]:
    url = f"{BASE_URL.rstrip('/')}/{path.lstrip('/')}"
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = requests.get(url, headers=HEADERS, params=params, timeout=20)
            if DEBUG:
                ct = r.headers.get("content-type", "")
                print(f"🔎 GET {url} | params={params} | status={r.status_code} | ct={ct}")
                if r.status_code != 200:
                    print(f"   body: {r.text[:400]}")
            if r.status_code == 200:
                # 1) normal JSON
                try:
                    return r.json()
                except Exception:
                    pass
                # 2) text that is JSON
                try:
                    return json.loads(r.text)
                except Exception:
                    pass
                # 3) trim leading junk, parse from first JSON token
                txt = r.text
                start = min([i for i in [txt.find("{"), txt.find("[")] if i != -1] or [-1])
                if start >= 0:
                    try:
                        return json.loads(txt[start:])
                    except Exception:
                        pass
                print(f"⚠️ Non-JSON response at {url}. First 400 chars:\n{r.text[:400]}")
                return None
            if r.status_code in RETRY_STATUS:
                time.sleep(RETRY_SLEEP * attempt)
                continue
            print(f"❌ GET {url} {r.status_code}: {r.text[:400]}")
            return None
        except requests.RequestException as e:
            print(f"⚠️ GET {url} error: {e}")
            time.sleep(RETRY_SLEEP * attempt)
    return None


def _mask(s: str, keep=4):
    if not s: return ""
    return s[:keep] + "…" if len(s) > keep else "****"

def smoke_test_sports():
    print("🔧 Config:", {
        "host": RAPIDAPI_HOST,
        "base_url": BASE_URL,
        "key(masked)": _mask(RAPIDAPI_KEY),
        "sports_path": ENDPOINTS["sports"],
    })
    data = http_get(ENDPOINTS["sports"])
    if not isinstance(data, dict):
        print("❌ Sports call didn’t return JSON. See debug above.")
        return None
    for key in ("sports", "data", "result", "items"):
        if isinstance(data.get(key), list):
            print(f"✅ sports list found under '{key}', len={len(data[key])}")
            return data[key]
    print(f"❌ JSON didn’t contain a list under expected keys. Keys: {list(data.keys())[:10]}")
    print("   Sample payload head:", json.dumps(data, indent=2)[:600])
    return None


# =========================
# 📚 API wrappers
# =========================
def fetch_sports() -> List[dict]:
    data = http_get(ENDPOINTS["sports"])
    return data.get("sports", []) if isinstance(data, dict) else []

def fetch_leagues(sport_id: int) -> List[dict]:
    params = {PARAM_KEYS["sport_id"]: sport_id}
    data = http_get(ENDPOINTS["leagues"], params=params)
    return data.get("leagues", []) if isinstance(data, dict) else []

def fetch_markets(sport_id: int, league_ids: Optional[List[int]] = None,
                  event_type: str = EVENT_TYPE, is_have_odds: bool = IS_HAVE_ODDS) -> List[dict]:
    params = {
        PARAM_KEYS["event_type"]: event_type,
        PARAM_KEYS["sport_id"]: sport_id,
        PARAM_KEYS["is_have_odds"]: str(bool(is_have_odds)).lower(),
    }
    if league_ids:
        params[PARAM_KEYS["league_ids"]] = ",".join(str(x) for x in league_ids)
    data = http_get(ENDPOINTS["markets"], params=params)
    if not isinstance(data, dict):
        return []
    for key in ("markets", "data", "result", "items"):
        if isinstance(data.get(key), list):
            return data[key]
    return []

def fetch_fixtures(sport_id: int, league_ids: List[int], is_live: Optional[bool] = None) -> Dict[int, dict]:
    params = {
        PARAM_KEYS["sport_id"]: sport_id,
        PARAM_KEYS["league_ids"]: ",".join(str(x) for x in league_ids) if league_ids else None,
        PARAM_KEYS["is_live"]: str(is_live).lower() if is_live is not None else None,
    }
    params = {k: v for k, v in params.items() if v is not None}
    data = http_get(ENDPOINTS["fixtures"], params=params)
    fixtures_by_id: Dict[int, dict] = {}
    if not isinstance(data, dict):
        return fixtures_by_id

    leagues = data.get("leagues") or []
    for lg in leagues:
        league_id = lg.get("id") or lg.get("leagueId")
        league_name = lg.get("name")
        for ev in lg.get("events", []) or []:
            ev_id = ev.get("id")
            if ev_id is None:
                continue
            fixtures_by_id[ev_id] = {
                "event_id": ev_id,
                "league_id": league_id,
                "league_name": league_name,
                "starts": ev.get("starts"),
                "home": ev.get("home"),
                "away": ev.get("away"),
                "state": ev.get("state"),
                "liveStatus": ev.get("liveStatus"),
            }
    return fixtures_by_id

def fetch_odds(sport_id: int, league_ids: List[int], odds_format: str = "DECIMAL",
               is_live: Optional[bool] = None) -> Dict[int, dict]:
    params = {
        PARAM_KEYS["sport_id"]: sport_id,
        PARAM_KEYS["league_ids"]: ",".join(str(x) for x in league_ids) if league_ids else None,
        PARAM_KEYS["is_live"]: str(is_live).lower() if is_live is not None else None,
        PARAM_KEYS["odds_format"]: odds_format,
        PARAM_KEYS["markets"]: "moneyline,spreads,totals",
    }
    params = {k: v for k, v in params.items() if v is not None}
    data = http_get(ENDPOINTS["odds"], params=params)
    odds_by_id: Dict[int, dict] = {}
    if not isinstance(data, dict):
        return odds_by_id
    for lg in data.get("leagues", []) or []:
        for ev in lg.get("events", []) or []:
            ev_id = ev.get("id")
            if ev_id is None:
                continue
            odds_by_id[ev_id] = ev
    return odds_by_id

# =========================
# 🧩 Merge & Normalize
# =========================
def extract_period(ev_odds: dict, want_number: int = 0) -> Optional[dict]:
    periods = ev_odds.get("periods") if isinstance(ev_odds, dict) else None
    if not isinstance(periods, list) or not periods:
        return None
    for p in periods:
        if p.get("number") == want_number:
            return p
    return periods[0]

def merge_fixture_odds_row(sport_name: str, league_obj: dict, fixture: dict, ev_odds: dict) -> dict:
    ts = now_iso()
    league_id   = league_obj.get("id") or league_obj.get("leagueId") or fixture.get("league_id")
    league_name = league_obj.get("name") or fixture.get("league_name")

    event_id = fixture.get("event_id")
    starts   = fixture.get("starts")
    home     = fixture.get("home")
    away     = fixture.get("away")
    is_live  = (fixture.get("liveStatus") == 1 or (fixture.get("state") or "").lower() == "live")

    # Defaults
    moneyline_home = moneyline_away = None
    spread_home_points = spread_home_price = None
    spread_away_points = spread_away_price = None
    total_points = total_over_price = total_under_price = None

    if ev_odds:
        period = extract_period(ev_odds, want_number=0) or {}

        # Moneyline
        ml = period.get("moneyline") or {}
        moneyline_home = to_float(ml.get("home"))
        moneyline_away = to_float(ml.get("away"))

        # Spreads
        spreads = period.get("spreads") or []
        if isinstance(spreads, list) and spreads:
            sp = spreads[0]
            spread_home_points = to_float(sp.get("hdp") or sp.get("points"))
            h = sp.get("home"); a = sp.get("away")
            spread_home_price = to_float(h.get("price") if isinstance(h, dict) else h)
            spread_away_price = to_float(a.get("price") if isinstance(a, dict) else a)

        # Totals
        totals = period.get("totals") or []
        if isinstance(totals, list) and totals:
            tot = totals[0]
            total_points = to_float(tot.get("points"))
            o = tot.get("over"); u = tot.get("under")
            total_over_price  = to_float(o.get("price") if isinstance(o, dict) else o)
            total_under_price = to_float(u.get("price") if isinstance(u, dict) else u)

    return {
        "timestamp": ts,
        "sport": sport_name,
        "league_id": league_id,
        "league_name": league_name,
        "event_id": event_id,
        "event_start": starts,
        "is_live": is_live,
        "home_team": home,
        "away_team": away,
        "moneyline_home": moneyline_home,
        "moneyline_away": moneyline_away,
        "spread_home_points": spread_home_points,
        "spread_home_price": spread_home_price,
        "spread_away_points": spread_away_points,
        "spread_away_price": spread_away_price,
        "total_points": total_points,
        "total_over_price": total_over_price,
        "total_under_price": total_under_price,
    }

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
    require_key()

    # --- Smoke test /sports first ---
    sports = smoke_test_sports()
    if not sports:
        sys.exit("❌ Could not fetch sports — verify RAPIDAPI_HOST, RAPIDAPI_KEY, and ENDPOINTS['sports'].")

    # If you want to proceed using the smoke test result:
    # If you normally filter by keywords/allowlists, keep your existing logic:
    if ALLOWLIST_SPORT_IDS:
        chosen_sports = [s for s in sports if s.get("id") in ALLOWLIST_SPORT_IDS]
    else:
        chosen_sports = filter_by_keywords(sports, "name", SPORT_NAME_KEYWORDS)

    if not chosen_sports:
        sys.exit("❌ No sports matched your filters. Adjust SPORT_NAME_KEYWORDS or ALLOWLIST_SPORT_IDS.")

    # ... rest of your existing main() ...


    # 1) Fetch sports & choose scope
    sports = fetch_sports()
    if not sports:
        sys.exit("❌ Could not fetch sports — check RAPIDAPI_KEY/host and product access.")

    if ALLOWLIST_SPORT_IDS:
        chosen_sports = [s for s in sports if s.get("id") in ALLOWLIST_SPORT_IDS]
    else:
        chosen_sports = filter_by_keywords(sports, "name", SPORT_NAME_KEYWORDS)

    if not chosen_sports:
        sys.exit("❌ No sports matched your filters. Adjust SPORT_NAME_KEYWORDS or ALLOWLIST_SPORT_IDS.")

    # 2) Fetch leagues per sport
    scope = []  # (sport_id, sport_name, [leagues])
    for s in chosen_sports:
        sport_id = s.get("id")
        sport_name = s.get("name")
        if not sport_id:
            continue
        leagues = fetch_leagues(sport_id)
        if not leagues:
            continue
        if ALLOWLIST_LEAGUE_IDS:
            leagues = [lg for lg in leagues if (lg.get("id") or lg.get("leagueId")) in ALLOWLIST_LEAGUE_IDS]
        else:
            leagues = filter_by_keywords(leagues, "name", LEAGUE_NAME_KEYWORDS)
        if leagues:
            scope.append((sport_id, sport_name, leagues))

    if not scope:
        sys.exit("❌ No leagues matched your filters. Adjust LEAGUE_NAME_KEYWORDS or ALLOWLIST_LEAGUE_IDS.")

    print("✅ Monitoring scope:")
    for sport_id, sport_name, leagues in scope:
        print(f"- {sport_name} [{sport_id}]: " + ", ".join([lg.get("name", str(lg.get('id'))) for lg in leagues[:8]]))

    # 3) Loop & write
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
            print(f"\n⏱️ Cycle {cycle} @ {now_iso()}")

            batch: List[dict] = []

            for sport_id, sport_name, leagues in scope:
                league_ids = [int(lg.get("id") or lg.get("leagueId")) for lg in leagues if (lg.get("id") or lg.get("leagueId")) is not None]
                if not league_ids:
                    continue

                # Use /markets to narrow to events that have odds now
                markets_list = fetch_markets(sport_id=sport_id, league_ids=league_ids, event_type=EVENT_TYPE, is_have_odds=IS_HAVE_ODDS)
                market_event_ids = set()
                for m in markets_list:
                    ev_id = m.get("event_id") or m.get("eventId") or m.get("id")
                    if ev_id is not None:
                        market_event_ids.add(int(ev_id))

                # Fixtures & odds (full game)
                fixtures_by_id = fetch_fixtures(sport_id, league_ids, is_live=None)
                odds_by_id     = fetch_odds(sport_id, league_ids, odds_format="DECIMAL", is_live=None)

                # Time window & allowlist filters
                for ev_id, fx in list(fixtures_by_id.items()):
                    if ALLOWLIST_EVENT_IDS and ev_id not in ALLOWLIST_EVENT_IDS:
                        fixtures_by_id.pop(ev_id, None); continue
                    if not within_upcoming_window(fx.get("starts"), ONLY_UPCOMING_HOURS):
                        fixtures_by_id.pop(ev_id, None)

                # If we have markets, keep only events present there (with odds)
                if market_event_ids:
                    fixtures_by_id = {eid: fx for eid, fx in fixtures_by_id.items() if int(eid) in market_event_ids}
                    odds_by_id     = {eid: od for eid, od in odds_by_id.items()     if int(eid) in market_event_ids}

                # Map leagues by id for label context
                leagues_by_id = {int(lg.get("id") or lg.get("leagueId")): lg for lg in leagues if (lg.get("id") or lg.get("leagueId")) is not None}

                # Merge rows
                for ev_id, fx in fixtures_by_id.items():
                    lg_id = int(fx.get("league_id")) if fx.get("league_id") is not None else None
                    league_obj = leagues_by_id.get(lg_id, {"id": lg_id, "name": None})
                    row = merge_fixture_odds_row(sport_name, league_obj, fx, odds_by_id.get(ev_id, {}))
                    batch.append(row)

                # Odds without fixture (edge)
                for ev_id, ev_od in odds_by_id.items():
                    if ev_id in fixtures_by_id:
                        continue
                    if ALLOWLIST_EVENT_IDS and ev_id not in ALLOWLIST_EVENT_IDS:
                        continue
                    stub_fx = {
                        "event_id": ev_id, "league_id": None, "league_name": None,
                        "starts": None, "home": ev_od.get("home"), "away": ev_od.get("away"),
                        "state": None, "liveStatus": None
                    }
                    row = merge_fixture_odds_row(sport_name, {"id": None, "name": None}, stub_fx, ev_od)
                    batch.append(row)

                time.sleep(0.2)  # be polite per sport

            if batch:
                write_rows(batch)
                print(f"💾 Wrote {len(batch)} rows.")
            else:
                print("↩️ Nothing to write this cycle.")

            time.sleep(POLL_INTERVAL)

    except KeyboardInterrupt:
        print("🛑 Stopped by user.")

if __name__ == "__main__":
    main()
