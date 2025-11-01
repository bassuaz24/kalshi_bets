#!/usr/bin/env python3
"""
Pinnacle (RapidAPI) markets ingestor — kit/v1/markets with rolling `since`.

- Uses RapidAPI headers (X-RapidAPI-Key, X-RapidAPI-Host)
- Polls markets for selected sport_id(s), event_type (prematch|live), and is_have_odds
- Persists `since` cursor per sport within session (optional: file persistence)
- Flattens period num_0 (full game) for: money_line, spreads, totals, team_total, meta.open_* flags
- Appends to daily CSV

Setup:
  python3 -m venv .venv && source .venv/bin/activate
  pip install requests pandas

Run:
  export RAPIDAPI_KEY="your_key"
  python pinnacle_markets_ingestor.py
"""

import os
import sys
import time
import json
import requests
import pandas as pd
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any
from wakepy import keep

with keep.running(on_fail='warn'):
    # Place your long-running Python script code here
    print("This script will continue running even with the lid closed.")
    # Example: time.sleep(3600) for a 1-hour task


    # =========================
    # 🔐 RapidAPI config — EDIT
    # =========================
    RAPIDAPI_KEY  = os.getenv("RAPIDAPI_KEY", "c6e3dfb23amsh78557ee88b89066p195550jsnc830a159ea59")
    RAPIDAPI_HOST = os.getenv("RAPIDAPI_HOST", "pinnacle-odds.p.rapidapi.com")
    BASE_URL      = f"https://{RAPIDAPI_HOST}"

    HEADERS = {
        "Accept": "application/json",
        "User-Agent": "pinnacle-rapidapi-markets/1.0",
        "X-RapidAPI-Key": RAPIDAPI_KEY,
        "X-RapidAPI-Host": RAPIDAPI_HOST,
    }

    def require_key():
        if not RAPIDAPI_KEY or RAPIDAPI_KEY == "YOUR_RAPIDAPI_KEY":
            sys.exit("❌ Set RAPIDAPI_KEY (env var or edit the script).")

    # =========================
    # 🎯 Scope & run controls — EDIT
    # =========================
    SPORTS_SCOPE = {
        3: [487, 493],   # Basketball → NBA only
        7: [889, 905],  # American Football → NFL only
    }
    EVENT_TYPE = "live"      # "prematch" | "live"
    IS_HAVE_ODDS = True          # True = only events that have periods (markets may still be closed)
    POLL_INTERVAL = 10           # seconds between cycles
    RUN_DURATION_MINUTES = None    # None for continuous
    USE_DAILY_FILE = True
    OUTPUT_DIR = "pinnacle_data_logs"
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    GLOBAL_CSV = os.path.join(OUTPUT_DIR, "pinnacle_markets_10/31.csv")

    # Optional: persist `since` between runs (set to a path or leave None for memory-only)
    SINCE_STATE_FILE = os.getenv("PINNACLE_SINCE_FILE", None)

    # Backoff
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
                r = requests.get(url, headers=HEADERS, params=params, timeout=25)
                if r.status_code == 200:
                    try:
                        return r.json()
                    except Exception:
                        # try lenient load
                        try:
                            return json.loads(r.text)
                        except Exception:
                            print(f"⚠️ Non-JSON at {url}: {r.text[:300]}", file=sys.stderr)
                            return None
                if r.status_code in RETRY_STATUS:
                    time.sleep(RETRY_SLEEP * attempt)
                    continue
                print(f"❌ GET {url} {r.status_code}: {r.text[:300]}", file=sys.stderr)
                return None
            except requests.RequestException as e:
                print(f"⚠️ GET {url} error: {e}", file=sys.stderr)
                time.sleep(RETRY_SLEEP * attempt)
        return None

    def load_since_state() -> Dict[str, int]:
        if SINCE_STATE_FILE and os.path.exists(SINCE_STATE_FILE):
            try:
                with open(SINCE_STATE_FILE, "r") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def save_since_state(state: Dict[str, int]):
        if not SINCE_STATE_FILE:
            return
        try:
            with open(SINCE_STATE_FILE, "w") as f:
                json.dump(state, f)
        except Exception as e:
            print(f"⚠️ Could not save since state: {e}", file=sys.stderr)

    # =========================
    # 📡 API: kit/v1/markets
    # =========================
    def fetch_markets(sport_id: int, since: Optional[int], event_type: str, is_have_odds: bool,
                    league_ids: Optional[List[int]] = None) -> Optional[dict]:
        params = {
            "sport_id": sport_id,
            "event_type": event_type,  # "prematch" or "live"
            "is_have_odds": str(bool(is_have_odds)).lower(),
        }
        if since:
            params["since"] = since
        if league_ids:
            params["league_ids"] = ",".join(str(x) for x in league_ids)

        return http_get("kit/v1/markets", params=params)

    # =========================
    # 🧩 Flattening helpers (period 0)
    # =========================
    def choose_first_spread(spreads: dict) -> (Optional[float], Optional[float], Optional[float]):
        """
        spreads is a dict keyed by string points (e.g., "-2.0") -> {hdp, home, away, ...}
        Choose the line closest to 0 handicap (common normalization).
        """
        if not isinstance(spreads, dict) or not spreads:
            return None, None, None
        # collect (abs(points), points, obj)
        candidates = []
        for k, v in spreads.items():
            # points may be in key (k) or in v["hdp"] / v["points"]
            p = to_float(v.get("hdp")) if isinstance(v, dict) else None
            if p is None:
                p = to_float(k)
            if p is None and isinstance(v, dict):
                p = to_float(v.get("points"))
            if p is None:
                continue
            candidates.append((abs(p), p, v))
        if not candidates:
            return None, None, None
        _, p_chosen, v = sorted(candidates, key=lambda t: t[0])[0]
        # prices can be plain float or nested price
        h = v.get("home"); a = v.get("away")
        home_price = to_float(h.get("price")) if isinstance(h, dict) else to_float(h)
        away_price = to_float(a.get("price")) if isinstance(a, dict) else to_float(a)
        return p_chosen, home_price, away_price

    def choose_first_total(totals: dict) -> (Optional[float], Optional[float], Optional[float]):
        """
        totals is a dict keyed by string points -> {points, over, under, ...}
        Choose the line closest to the median (we'll just take the smallest absolute points).
        """
        if not isinstance(totals, dict) or not totals:
            return None, None, None
        candidates = []
        for k, v in totals.items():
            p = to_float(v.get("points")) if isinstance(v, dict) else to_float(k)
            if p is None:
                continue
            candidates.append((abs(p), p, v))
        if not candidates:
            return None, None, None
        _, p_chosen, v = sorted(candidates, key=lambda t: t[0])[0]
        o = v.get("over"); u = v.get("under")
        over_price = to_float(o.get("price")) if isinstance(o, dict) else to_float(o)
        under_price = to_float(u.get("price")) if isinstance(u, dict) else to_float(u)
        return p_chosen, over_price, under_price

    def flatten_event(e: dict, sport_name: Optional[str]) -> dict:
        """
        Flatten one event’s period num_0 into a single row.
        """
        ts = now_iso()
        event_id   = e.get("event_id")
        league_id  = e.get("league_id")
        league_nm  = e.get("league_name")
        starts     = e.get("starts")
        event_type = e.get("event_type")
        live_id    = e.get("live_status_id")
        home       = e.get("home")
        away       = e.get("away")
        is_have_odds = e.get("is_have_odds") or e.get("is_have_periods")
        periods    = e.get("periods") or {}
        p0         = periods.get("num_0") or {}

        # Money line
        ml = p0.get("money_line") or p0.get("moneyLine") or {}
        ml_home = to_float(ml.get("home"))
        ml_draw = to_float(ml.get("draw"))
        ml_away = to_float(ml.get("away"))

        # Spreads (choose nearest to 0)
        spreads = p0.get("spreads") or {}
        sp_points, sp_home_price, sp_away_price = choose_first_spread(spreads)

        # Totals (choose smallest abs points)
        totals = p0.get("totals") or {}
        tot_points, tot_over_price, tot_under_price = choose_first_total(totals)

        # Team totals (home/away)
        team_total = p0.get("team_total") or {}
        tt_home = team_total.get("home") or {}
        tt_away = team_total.get("away") or {}
        tt_home_points = to_float(tt_home.get("points"))
        tt_home_over  = to_float(tt_home.get("over"))
        tt_home_under = to_float(tt_home.get("under"))
        tt_away_points = to_float(tt_away.get("points"))
        tt_away_over  = to_float(tt_away.get("over"))
        tt_away_under = to_float(tt_away.get("under"))

        # Meta (open flags)
        meta = p0.get("meta") or {}
        open_ml     = bool(meta.get("open_money_line"))
        open_spread = bool(meta.get("open_spreads"))
        open_total  = bool(meta.get("open_totals"))
        open_team   = bool(meta.get("open_team_total"))

        return {
            "timestamp": ts,
            "sport_name": sport_name,
            "sport_id": e.get("sport_id"),
            "league_id": league_id,
            "league_name": league_nm,
            "event_id": event_id,
            "event_type": event_type,         # prematch | live
            "live_status_id": live_id,        # 0/1/2
            "starts": starts,
            "home_team": home,
            "away_team": away,
            "is_have_odds": bool(is_have_odds),

            # Moneyline (decimal)
            "moneyline_home": ml_home,
            "moneyline_draw": ml_draw,
            "moneyline_away": ml_away,

            # Spread (closest to 0)
            "spread_points": sp_points,
            "spread_home_price": sp_home_price,
            "spread_away_price": sp_away_price,

            # Total (closest to center)
            "total_points": tot_points,
            "total_over_price": tot_over_price,
            "total_under_price": tot_under_price,

            # Team totals
            "team_total_home_points": tt_home_points,
            "team_total_home_over":   tt_home_over,
            "team_total_home_under":  tt_home_under,
            "team_total_away_points": tt_away_points,
            "team_total_away_over":   tt_away_over,
            "team_total_away_under":  tt_away_under,

            # Market open flags (period meta)
            "open_money_line": open_ml,
            "open_spreads": open_spread,
            "open_totals": open_total,
            "open_team_total": open_team,
        }

    # =========================
    # 💾 Writer
    # =========================
    def write_rows(rows: List[dict]):
        if not rows:
            return
        if USE_DAILY_FILE:
            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            path = os.path.join(OUTPUT_DIR, f"pinnacle_markets_{date_str}.csv")
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
        since_state: Dict[str, int] = load_since_state()  # key = str(sport_id)

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

                for sid, league_ids in SPORTS_SCOPE.items():
                    since = since_state.get(str(sid))
                    params_info = {
                        "sport_id": sid,
                        "event_type": EVENT_TYPE,
                        "is_have_odds": IS_HAVE_ODDS,
                        "since": since,
                        "league_ids": league_ids,
                    }
                    print(f"📡 markets query: {params_info}")

                    payload = fetch_markets(sid, since, EVENT_TYPE, IS_HAVE_ODDS, league_ids=league_ids)
                    if not isinstance(payload, dict):
                        print(f"⚠️ No payload for sport_id={sid}")
                        time.sleep(0.2)
                        continue

                    # Update since cursor (always set latest)
                    last_ts = payload.get("last")
                    if isinstance(last_ts, int):
                        since_state[str(sid)] = last_ts

                    sport_name = payload.get("sport_name")
                    events = payload.get("events") or []
                    print(f"   ➜ {sport_name or sid}: {len(events)} changed event(s)")

                    for e in events:
                        try:
                            row = flatten_event(e, sport_name)
                            # Optional event-level gate: only log if any market open OR any price present
                            if any([
                                row.get("moneyline_home") is not None,
                                row.get("moneyline_away") is not None,
                                row.get("moneyline_draw") is not None,
                                row.get("spread_home_price") is not None,
                                row.get("total_over_price") is not None,
                                row.get("open_money_line"),
                                row.get("open_spreads"),
                                row.get("open_totals")
                            ]):
                                batch.append(row)
                        except Exception as ex:
                            # Don’t let a single event kill the batch
                            print(f"⚠️ flatten error event_id={e.get('event_id')}: {ex}", file=sys.stderr)
                            continue

                    time.sleep(0.25)  # polite per sport

                if batch:
                    write_rows(batch)
                    save_since_state(since_state)
                    print(f"💾 Wrote {len(batch)} row(s). since_state={since_state}")
                else:
                    print("↩️ Nothing to write this cycle.")

                time.sleep(POLL_INTERVAL)

        except KeyboardInterrupt:
            print("🛑 Stopped by user.")
            save_since_state(since_state)


    if __name__ == "__main__":
        sports = http_get("kit/v1/sports")
        if sports:
            print("📋 Available Sports:")
            for s in sports:
                print(f" - {s['id']}: {s['name']}")
        else:
            print("⚠️ Could not load sports list.")

        main()

