#!/usr/bin/env python3
"""
kalshi_live_trader_observed.py
--------------------------------
Hardcoded-config, DRY-RUN live loop that:
- Discovers real market tickers from SERIES (no env vars needed).
- Parses orderbooks in both dollars and cents formats (robust BBO).
- Uses a basic, inclusive strategy so you WILL see decisions & "orders" in DRY_RUN.
- Writes decisions and orders to CSV in ./live_betting/.
- Avoids account endpoints (/me, /positions, /fills) which often 404 on elections host.
- Prints a heartbeat each loop with counts.

Run:
  python3 kalshi_live_trader_observed.py
"""

# =========================
# ======  CONFIG  =========
# =========================

class CONFIG:
    # --- API host & key ---
    HOST = "https://api.elections.kalshi.com"    # default host (per migration notice)
    HOST_FALLBACKS = [
        "https://trading-api.kalshi.com",
        "https://api.kalshi.com",
        "https://trading.kalshi.com",
    ]
    API_KEY = "KALSHI_API_KEY"      # prefer env KALSHI_API_KEY or --api-key
    AUTH_MODE = "kalshi_pss"                          # "kalshi_pss", "bearer" or "x_api_key"
    PRIVATE_KEY_PATH = None                           # set or use env KALSHI_PRIVATE_KEY_PATH

    # --- Discovery scope (college basketball spreads/totals only by default; edit to add more) ---
    SERIES = ["KXNCAAMBGAME", "KXNCAAMBSPREAD"]   # adjust this list to include other markets if desired
    TICKERS = []                   # set explicit tickers to bypass series discovery
    MAX_DISCOVERY_PAGES = 20
    DISCOVERY_SAMPLE_ROWS = 10

    # Client-side status allowlist — only trade open markets
    ALLOWED_STATUSES = {"active", "open"}   # set to None to accept any status

    # --- Behavior toggles ---
    DRY_RUN = True              # simulate by default; use --live flag to send real orders
    POLL_INTERVAL_SEC = 4        # seconds between loops
    REQ_TIMEOUT = 15             # HTTP timeout
    MAX_RETRIES = 3
    RETRY_SLEEP = 1.0            # backoff base seconds
    TICKERS_PER_LOOP = 60        # process at most this many tickers per loop (for visibility)

    MIN_TICK = 0.01
    MIN_PRICE = 0.01
    MAX_PRICE = 0.99

    # --- Data-driven analysis (see data_analysis.ipynb) ---
    class DATA:
        DATE = "2025-11-25"                        # optional YYYY-MM-DD to lock to a folder; None picks latest
        ODDS_SPORT = "cbb"                 # oddsapi sport prefix (e.g., cbb, cfb, nba, nfl)
        KALSHI_SPORT = "ncaab"             # kalshi sport prefix (e.g., ncaab, ncaaf, nba, nfl)
        ODDS_DIR = "data_collection/updated_scripts/oddsapi_outputs"
        KALSHI_DIR = "data_collection/updated_scripts/kalshi_data_logs"
        OUTPUT_DIR = "live_betting/analysis_outputs"
        EDGE_WINNERS = 0.00
        EDGE_SPREADS = 0.01
        WINNERS_EV_THRESHOLD = 0.15
        SPREADS_EV_THRESHOLD = 0.0
        TOTAL_BANKROLL = None              # None => pull from account; else use this float
        WINNERS_PROPORTION = 0.7
        SPREADS_PROPORTION = 0.3
        KELLY_CAP = 1.0
        Q1_WEIGHT = 1.0
        Q2_WEIGHT = 1.0
        Q3_WEIGHT = 1.0
        Q4_WEIGHT = 1.0

    # --- Logging ---
    LOG_DIR = "live_betting"
    HEARTBEAT_PRINT = False        # suppress per-loop ticker prints; orders print when placed
    DEBUG_SKIPS = True             # write skip reasons to health_log.csv


# =========================
# ======  IMPORTS  ========
# =========================

import sys
import time
import json
import csv
import math
import uuid
import hashlib
import signal
import os
import argparse
import requests
import re
from collections import defaultdict
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
ANALYSIS_SIGNALS: Dict[str, Dict[str, Any]] = {}
ANALYSIS_SUMMARY: Dict[str, Dict[str, float]] = {}
ANALYSIS_CSVS: Dict[str, str] = {}


# =========================
# =====  UTIL/LOGGING  ====
# =========================

def now_utc() -> datetime:
    return datetime.now(timezone.utc)

def now_iso() -> str:
    return now_utc().isoformat().replace("+00:00", "Z")

def to_f(x) -> Optional[float]:
    try:
        return float(x)
    except Exception:
        return None

def clamp_price(p: float) -> Optional[float]:
    if p is None or math.isnan(p):
        return None
    p = max(CONFIG.MIN_PRICE, min(CONFIG.MAX_PRICE, p))
    # snap to tick
    return round(round(p / CONFIG.MIN_TICK) * CONFIG.MIN_TICK, 4)

def _ensure_dir(path: str):
    import os
    os.makedirs(path, exist_ok=True)

def log_csv(filename: str, fieldnames: List[str], row: Dict[str, Any]):
    _ensure_dir(CONFIG.LOG_DIR)
    import os
    path = f"{CONFIG.LOG_DIR}/{filename}"
    is_new = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        if is_new:
            w.writeheader()
        w.writerow(row)

def log_health(msg: str, **kw):
    row = {"ts": now_iso(), "msg": msg}
    row.update(kw)
    fields = ["ts", "msg"] + sorted([k for k in row.keys() if k not in {"ts","msg"}])
    log_csv("health_log.csv", fields, row)

def log_decision(ticker: str, intent: Dict[str, Any], ctx: Dict[str, Any]):
    row = {
        "ts": now_iso(),
        "ticker": ticker,
        "intent_id": intent.get("intent_id"),
        "side": intent.get("side"),
        "price": intent.get("price"),
        "count": intent.get("count"),
        "reason": intent.get("reason"),
        "fair_yes": ctx.get("fair_yes"),
        "edge": ctx.get("edge"),
        "spread": ctx.get("spread"),
        "yes_bid": ctx.get("yes_bid"),
        "yes_ask": ctx.get("yes_ask"),
        "no_bid": ctx.get("no_bid"),
        "no_ask": ctx.get("no_ask"),
    }
    fields = ["ts","ticker","intent_id","side","price","count","reason",
              "fair_yes","edge","spread","yes_bid","yes_ask","no_bid","no_ask"]
    log_csv("decisions_log.csv", fields, row)

def log_order(action: str, payload: Dict[str, Any], resp: Any):
    row = {
        "ts": now_iso(),
        "action": action,
        "ticker": payload.get("ticker"),
        "side": payload.get("side"),
        "type": payload.get("type"),
        "price": payload.get("price"),
        "count": payload.get("count"),
        "tif": payload.get("time_in_force"),
        "client_order_id": payload.get("client_order_id"),
        "result": json.dumps(resp)[:1800]
    }
    fields = ["ts","action","ticker","side","type","price","count","tif","client_order_id","result"]
    log_csv("orders_log.csv", fields, row)


# =========================
# ===== HTTP / CLIENT =====
# =========================

def _load_private_key(path: str):
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.backends import default_backend
    except ImportError:
        sys.exit("❌ Missing dependency 'cryptography'. Install with: pip install cryptography")
    with open(path, "rb") as key_file:
        return serialization.load_pem_private_key(key_file.read(), password=None, backend=default_backend())

def _sign_pss_text(private_key, text: str) -> str:
    try:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding
    except ImportError:
        sys.exit("❌ Missing dependency 'cryptography'. Install with: pip install cryptography")
    message = text.encode("utf-8")
    signature = private_key.sign(
        message,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256(),
    )
    import base64
    return base64.b64encode(signature).decode("utf-8")

def _headers(extra: Optional[Dict[str,str]] = None, method: Optional[str] = None, path: Optional[str] = None) -> Dict[str,str]:
    base = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "kalshi-live-trader-observed/1.0",
    }

    mode = (CONFIG.AUTH_MODE or "").lower()
    api_key_clean = (CONFIG.API_KEY or "").strip().replace("\n", "")

    if mode == "kalshi_pss":
        key_id = api_key_clean
        priv_path = os.getenv("KALSHI_PRIVATE_KEY_PATH") or CONFIG.PRIVATE_KEY_PATH
        if not key_id or key_id == "PUT_YOUR_KALSHI_API_KEY_HERE":
            sys.exit("❌ Set key id via KALSHI_API_KEY or --api-key (used as KALSHI-ACCESS-KEY).")
        if not priv_path:
            sys.exit("❌ Set private key path via KALSHI_PRIVATE_KEY_PATH or CONFIG.PRIVATE_KEY_PATH.")
        priv_path = os.path.expanduser(priv_path)
        if not os.path.exists(priv_path):
            sys.exit(f"❌ Private key file not found: {priv_path}")
        if not method or not path:
            sys.exit("❌ Internal error: method/path required for kalshi_pss signing.")
        ts = str(int(time.time() * 1000))
        # Kalshi expects the string to include the /trade-api/v2 prefix.
        path_clean = "/trade-api/v2/" + path.lstrip("/").split("?")[0]
        msg = ts + method.upper() + path_clean
        priv = _load_private_key(priv_path)
        sig = _sign_pss_text(priv, msg)
        base.update({
            "KALSHI-ACCESS-KEY": key_id,
            "KALSHI-ACCESS-SIGNATURE": sig,
            "KALSHI-ACCESS-TIMESTAMP": ts,
        })
    elif mode == "bearer":
        if not api_key_clean or api_key_clean == "PUT_YOUR_KALSHI_API_KEY_HERE":
            sys.exit("❌ Please set a valid bearer token via KALSHI_API_KEY or --api-key.")
        base["Authorization"] = f"Bearer {api_key_clean}"
    else:  # x_api_key
        if not api_key_clean or api_key_clean == "PUT_YOUR_KALSHI_API_KEY_HERE":
            sys.exit("❌ Please set a valid API key via KALSHI_API_KEY or --api-key.")
        base["X-API-Key"] = api_key_clean

    if extra:
        base.update(extra)
    return base

def http_request(method: str, path: str, params: Optional[dict] = None,
                 body: Optional[dict] = None, extra_headers: Optional[dict] = None) -> Optional[dict]:
    url = f"{CONFIG.HOST.rstrip('/')}/trade-api/v2/{path.lstrip('/')}"
    data = json.dumps(body) if body is not None else None

    for attempt in range(1, CONFIG.MAX_RETRIES + 1):
        try:
            if CONFIG.DRY_RUN and method.upper() in {"POST","DELETE"}:
                # emulate success
                return {"dry_run": True, "echo": {"path": path, "params": params, "body": body}}
            r = requests.request(method.upper(), url, headers=_headers(extra_headers, method=method, path=path),
                                 params=params, data=data, timeout=CONFIG.REQ_TIMEOUT)
            if r.status_code in (200, 201, 204):
                if r.status_code == 204 or not r.text.strip():
                    return {"ok": True}
                try:
                    return r.json()
                except Exception:
                    return json.loads(r.text)
            if r.status_code in {429, 500, 502, 503, 504}:
                time.sleep(CONFIG.RETRY_SLEEP * attempt)
                continue
            print(f"❌ {method} {url} {r.status_code}: {r.text[:300]}")
            return None
        except requests.RequestException as e:
            print(f"⚠️ {method} {url} error: {e}")
            time.sleep(CONFIG.RETRY_SLEEP * attempt)
    return None

def list_markets(series_ticker: Optional[str] = None, status: Optional[str] = None, cursor: Optional[str] = None) -> dict:
    params = {}
    if series_ticker:
        params["series_ticker"] = series_ticker
    if status:
        params["status"] = status
    if cursor:
        params["cursor"] = cursor
    return http_request("GET", "markets", params=params) or {}

def get_market(ticker: str) -> dict:
    return http_request("GET", f"markets/{ticker}") or {}

def get_orderbook(ticker: str) -> dict:
    return http_request("GET", f"markets/{ticker}/orderbook") or {}

def place_order(payload: Dict[str,Any], idem_key: str) -> Optional[dict]:
    """
    Kalshi docs: POST https://{host}/trade-api/v2/portfolio/orders
    """
    return http_request("POST", "portfolio/orders", body=payload, extra_headers={"Idempotency-Key": idem_key})


def fetch_total_bankroll() -> Optional[float]:
    """
    Pull cash balance from account. Returns None on failure.
    """
    try:
        payload = http_request("GET", "portfolio/balance")
        if payload and isinstance(payload, dict):
            val = to_f(payload.get("balance") or payload.get("cash_balance") or payload.get("portfolio_value"))
            if val is not None and val > 0:
                return val
    except Exception as e:
        log_health("balance_fetch_error", error=str(e))
    return None


# =========================
# ==== NORMALIZATION ======
# =========================

def normalize_market(market: dict) -> dict:
    """
    API responses for GET /markets/{ticker} wrap the market under a 'market' key.
    Normalize so downstream logic can always expect the fields at top-level.
    """
    if isinstance(market, dict) and "market" in market and isinstance(market["market"], dict):
        return market["market"]
    return market or {}


def parse_event_start(market: dict) -> Optional[datetime]:
    """
    Try to parse the scheduled event start time from the market payload.
    """
    if not market:
        return None
    raw = market.get("event_start_time") or market.get("event_expiration_time") or market.get("close_time") or market.get("expiry")
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None


# =========================
# ======= DISCOVERY =======
# =========================

def _collect_all_for_series(series: str) -> list:
    """Fetch all markets for a series (no server-side status filter)."""
    out, cursor, pages = [], None, 0
    while pages < CONFIG.MAX_DISCOVERY_PAGES:
        pages += 1
        payload = list_markets(series_ticker=series, status=None, cursor=cursor)
        markets = payload.get("markets") or []
        out.extend(markets)
        cursor = payload.get("cursor")
        if not cursor:
            break
    return out

def _debug_dump_series(series: str, markets: list, max_rows: int):
    if not markets:
        print(f"🔎 series={series}: 0 markets")
        return
    print(f"🔎 series={series}: total {len(markets)} markets (no status filter). Sample:")
    for m in markets[:max_rows]:
        t = m.get("ticker")
        st = (m.get("status") or "").lower()
        ttl = (m.get("title") or "")[:60]
        print(f"   - {t}  status={st}  title={ttl}")

def discover_market_tickers(series_list: List[str]) -> List[str]:
    """Discover actual market tickers from series, then client-filter by CONFIG.ALLOWED_STATUSES (or keep all)."""
    discovered = []
    for s in series_list:
        ms = _collect_all_for_series(s)
        _debug_dump_series(s, ms, CONFIG.DISCOVERY_SAMPLE_ROWS)
        statuses_seen = sorted(set((m.get("status") or "").lower() for m in ms))
        if not ms:
            print(f"   ⚠️ No markets returned by API for series={s}.")
        kept = []
        for m in ms:
            t = m.get("ticker")
            st = (m.get("status") or "").lower()
            if not t:
                continue
            if CONFIG.ALLOWED_STATUSES is None or st in CONFIG.ALLOWED_STATUSES:
                kept.append(t)
        if not kept and CONFIG.ALLOWED_STATUSES is not None:
            print(f"   ⚠️ After filtering by {sorted(CONFIG.ALLOWED_STATUSES)}, none kept for series={s}.")
            print(f"      Statuses seen: {statuses_seen}")
        discovered.extend(kept)
    return sorted(set(discovered))


# =========================
# ======= BBO / OB  =======
# =========================

def parse_ob_yes_no(orderbook: dict) -> Tuple[List[Tuple[float,int]], List[Tuple[float,int]]]:
    """
    Return YES and NO ask ladders in dollars as lists of (price, qty).
    Supports both:
      - dollars: orderbook["orderbook"]["yes_dollars"] / ["no_dollars"] (e.g., [[0.63, 10], ...])
      - cents:   orderbook["orderbook"]["yes"] / ["no"] (e.g., [[63, 10], ...])  -> divide by 100
    """
    ob = (orderbook or {}).get("orderbook") or {}

    def _norm_levels(levels, cents: bool) -> List[Tuple[float,int]]:
        out = []
        if isinstance(levels, list):
            for it in levels:
                if isinstance(it, (list, tuple)) and len(it) == 2:
                    p_raw, q_raw = it
                    # price
                    try:
                        p = float(p_raw)
                    except Exception:
                        continue
                    if cents:
                        p = p / 100.0
                    # qty
                    try:
                        q = int(q_raw)
                    except Exception:
                        continue
                    if q > 0:
                        out.append((round(p, 4), q))
        out.sort(key=lambda t: t[0])  # asks ascending
        return out

    yes_levels = _norm_levels(ob.get("yes_dollars"), cents=False)
    no_levels  = _norm_levels(ob.get("no_dollars"),  cents=False)

    if not yes_levels and isinstance(ob.get("yes"), list):
        yes_levels = _norm_levels(ob.get("yes"), cents=True)
    if not no_levels and isinstance(ob.get("no"), list):
        no_levels = _norm_levels(ob.get("no"), cents=True)

    return yes_levels, no_levels

def best_prices_from_sources(market: dict, orderbook: dict) -> Dict[str, Optional[float]]:
    """
    Build a consistent top-of-book using market fields or orderbook ladders with parity:
      - yes_ask from market.yes_ask_dollars OR ob.yes_dollars[0][0] OR ob.yes[0][0]/100
      - no_ask  from market.no_ask_dollars  OR ob.no_dollars[0][0]  OR ob.no[0][0]/100
      - yes_bid from market.yes_bid_dollars OR 1 - no_ask
      - no_bid  from market.no_bid_dollars  OR 1 - yes_ask
    """
    # from market (may be missing)
    yb_m = to_f(market.get("yes_bid_dollars"))
    ya_m = to_f(market.get("yes_ask_dollars"))
    nb_m = to_f(market.get("no_bid_dollars"))
    na_m = to_f(market.get("no_ask_dollars"))

    # from orderbook (asks)
    yes_levels, no_levels = parse_ob_yes_no(orderbook)
    ya_ob = yes_levels[0][0] if yes_levels else None
    na_ob = no_levels[0][0]  if no_levels  else None

    yes_ask = ya_m if ya_m is not None else ya_ob
    no_ask  = na_m if na_m is not None else na_ob

    yes_bid = yb_m if yb_m is not None else (1.0 - no_ask if no_ask is not None else None)
    no_bid  = nb_m if nb_m is not None else (1.0 - yes_ask if yes_ask is not None else None)

    def clamp01(x):
        return None if x is None else max(CONFIG.MIN_PRICE, min(CONFIG.MAX_PRICE, round(float(x), 4)))

    return {
        "yes_bid": clamp01(yes_bid),
        "yes_ask": clamp01(yes_ask),
        "no_bid":  clamp01(no_bid),
        "no_ask":  clamp01(no_ask),
    }


# =========================
# == DATA ANALYSIS LOGIC ==
# =========================

def _require_analysis_deps():
    """
    Import pandas/numpy and return a fuzzy matcher. Exits early with a helpful
    message if the data-analysis stack is missing.
    """
    try:
        import pandas as pd
        import numpy as np
    except ImportError as e:
        sys.exit(f"❌ Missing dependency for data analysis: {e}. Install pandas/numpy to continue.")

    try:
        from rapidfuzz.fuzz import ratio as fuzz_ratio
    except ImportError:
        # Lightweight fallback to avoid hard-failing if rapidfuzz is absent.
        from difflib import SequenceMatcher
        def fuzz_ratio(a, b):
            return int(100 * SequenceMatcher(None, str(a), str(b)).ratio())

    return pd, np, fuzz_ratio


def _latest_csv(base_dir: Path, sport_prefix: str, suffix: str, date_hint: Optional[str]) -> Optional[Path]:
    """
    Return the newest CSV for a specific date. If date_hint is provided, only
    search that date subdirectory. If date_hint is None, search the whole tree.
    """
    search_base = base_dir / date_hint if date_hint else base_dir
    if not search_base.exists():
        return None
    pattern = f"{sport_prefix}_{suffix}*.csv"
    candidates = list(search_base.rglob(pattern))
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _clean_team_name(name: Optional[str]) -> Optional[str]:
    if name is None or not isinstance(name, str):
        return None
    return re.sub(r"\bSt\.$", "St", name.strip())


def build_filtered_frames(date_override: Optional[str], bankroll_winners: float, bankroll_spreads: float):
    """
    Rebuild filtered_winners_df and filtered_spreads_df inside the trader using
    the notebook logic. Returns a tuple (filtered_winners_df, filtered_spreads_df).
    """
    pd, np, fuzz_ratio = _require_analysis_deps()

    odds_dir = REPO_ROOT / CONFIG.DATA.ODDS_DIR
    kalshi_dir = REPO_ROOT / CONFIG.DATA.KALSHI_DIR
    date_str = date_override or CONFIG.DATA.DATE or now_utc().date().isoformat()

    odds_path = _latest_csv(odds_dir, CONFIG.DATA.ODDS_SPORT, "odds", date_str)
    winners_path = _latest_csv(kalshi_dir, CONFIG.DATA.KALSHI_SPORT, "winners", date_str)
    spreads_path = _latest_csv(kalshi_dir, CONFIG.DATA.KALSHI_SPORT, "spreads", date_str)

    if not odds_path or not winners_path:
        log_health("analysis_missing_inputs",
                   odds_path=str(odds_path) if odds_path else None,
                   winners_path=str(winners_path) if winners_path else None,
                   spreads_path=str(spreads_path) if spreads_path else None,
                   date=date_str)
        sys.exit(f"❌ Missing required CSVs for date={date_str}. odds={odds_path} winners={winners_path}")

    odds_df = pd.read_csv(odds_path)
    if "league" in odds_df.columns:
        odds_df = odds_df.drop(columns=["league"])
    odds_df = odds_df.rename(columns={"price": "odds"})
    odds_df["odds"] = pd.to_numeric(odds_df["odds"], errors="coerce")
    odds_df["point"] = pd.to_numeric(odds_df.get("point"), errors="coerce")
    odds_df["vig_prob"] = 1 / odds_df["odds"]

    # Filter out games that have already started based on odds start_time.
    def _future_only(df):
        if "start_time" not in df.columns:
            return df
        ts = pd.to_datetime(df["start_time"], utc=True, errors="coerce")
        mask = ts.isna() | (ts > pd.Timestamp.utcnow())
        return df.loc[mask].copy()

    odds_df = _future_only(odds_df)

    def remove_vig_probs(df):
        df = df.copy()
        df["fair_prb"] = pd.NA
        grouped = df.groupby(["game_id", "bookmaker", "market"])
        for _, group in grouped:
            if len(group) < 2:
                continue
            probs = group["vig_prob"]
            total = probs.sum()
            if total and total > 0:
                fair_probs = (probs / total).round(4)
                df.loc[group.index, "fair_prb"] = fair_probs
        return df

    odds_df = remove_vig_probs(odds_df)

    odds_winners_df = odds_df[odds_df["market"] == "h2h"].copy()
    odds_spreads_df = odds_df[odds_df["market"] == "spreads"].copy()
    odds_spreads_df = odds_spreads_df.loc[(odds_spreads_df["point"].notna()) & (odds_spreads_df["point"] > 0)]
    odds_totals_df = odds_df[odds_df["market"] == "totals"].copy()

    # Ensure fair probability columns are numeric for downstream math.
    for _df in (odds_winners_df, odds_spreads_df, odds_totals_df):
        if "fair_prb" in _df.columns:
            _df["fair_prb"] = pd.to_numeric(_df["fair_prb"], errors="coerce")

    mask = odds_winners_df["fair_prb"].notna()
    avg_by_team = (
        odds_winners_df.loc[mask]
        .groupby(["game_id", "team"])["fair_prb"]
        .transform("median")
        .round(4)
    )
    odds_winners_df.loc[mask, "avg_fair_prb"] = avg_by_team
    odds_winners_df.loc[~mask, "avg_fair_prb"] = pd.NA

    mask = odds_spreads_df["fair_prb"].notna()
    avg_by_point = (
        odds_spreads_df.loc[mask]
        .groupby(["game_id", "point", "team"])["fair_prb"]
        .transform("mean")
        .round(4)
    )
    odds_spreads_df["avg_fair_prb"] = avg_by_point

    mask = odds_totals_df["fair_prb"].notna()
    avg_by_tot_point = (
        odds_totals_df.loc[mask]
        .groupby(["game_id", "point", "team"])["fair_prb"]
        .transform("mean")
        .round(4)
    )
    odds_totals_df["avg_fair_prb"] = avg_by_tot_point

    kalshi_winners_df = pd.read_csv(winners_path)
    kalshi_spreads_df = pd.read_csv(spreads_path) if spreads_path else pd.DataFrame()

    if kalshi_spreads_df.empty and spreads_path:
        log_health("analysis_empty_spreads", path=str(spreads_path))

    columns_to_drop = [
        "timestamp", "market_type", "yes_bid2", "yes_ask2", "no_bid2", "no_ask2",
        "yes_depth_bids", "yes_depth_asks", "no_depth_bids", "no_depth_asks"
    ]
    kalshi_winners_df = kalshi_winners_df.drop(columns=[c for c in columns_to_drop if c in kalshi_winners_df.columns])
    if not kalshi_spreads_df.empty:
        kalshi_spreads_df = kalshi_spreads_df.drop(columns=[c for c in columns_to_drop if c in kalshi_spreads_df.columns])

    for col in ["yes_bid", "yes_ask", "no_bid", "no_ask"]:
        if col in kalshi_winners_df.columns:
            kalshi_winners_df[col] = pd.to_numeric(kalshi_winners_df[col], errors="coerce")
        if not kalshi_spreads_df.empty and col in kalshi_spreads_df.columns:
            kalshi_spreads_df[col] = pd.to_numeric(kalshi_spreads_df[col], errors="coerce")

    def extract_teams_from_winners(title):
        if not isinstance(title, str):
            return pd.Series([None, None])
        title = title.replace(" Winner?", "")
        if " at " in title:
            right, left = title.split(" at ", 1)
        elif " vs " in title:
            right, left = title.split(" vs ", 1)
        else:
            return pd.Series([None, None])
        return pd.Series([_clean_team_name(left), _clean_team_name(right)])

    kalshi_winners_df[["home_team", "away_team"]] = kalshi_winners_df["title"].apply(extract_teams_from_winners)

    kalshi_spreads_df["team"] = kalshi_spreads_df["title"].apply(
        lambda t: _clean_team_name(t.split(" wins by ", 1)[0]) if isinstance(t, str) and " wins by " in t else None
    ) if not kalshi_spreads_df.empty else pd.Series(dtype=object)

    if CONFIG.DATA.KALSHI_SPORT == "ncaaf":
        kalshi_spreads_df["points"] = kalshi_spreads_df["title"].str.extract(r"over ([\d.]+) points\?").astype(float) if not kalshi_spreads_df.empty else pd.Series(dtype=float)
    elif CONFIG.DATA.KALSHI_SPORT in {"ncaab", "nba"}:
        kalshi_spreads_df["points"] = kalshi_spreads_df["title"].str.extract(r"over ([\d.]+) Points\?").astype(float) if not kalshi_spreads_df.empty else pd.Series(dtype=float)

    kalshi_winners_teams = pd.unique(
        kalshi_winners_df.drop_duplicates(subset=["home_team", "away_team"])[["home_team", "away_team"]].values.ravel()
    )
    kalshi_spreads_teams = kalshi_spreads_df["team"].drop_duplicates().tolist() if not kalshi_spreads_df.empty else []

    odds_teams_by_market = odds_df.groupby("market")["team"].unique().to_dict()

    def fuzzy_match_kalshi_to_odds(kalshi_teams, odds_team_names):
        matched_kalshi = []
        matched_odds = []
        candidates_dict = defaultdict(list)

        kalshi_sorted = sorted([k for k in kalshi_teams if isinstance(k, str)], key=lambda x: x[0] if x else "")
        remaining_odds = sorted([o for o in odds_team_names.tolist() if isinstance(o, str)])

        for kalshi_name in kalshi_sorted:
            candidates = []
            for odds_name in remaining_odds:
                if kalshi_name and kalshi_name in odds_name:
                    candidates.append(odds_name)
            if len(candidates) == 1:
                candidates_dict[candidates[0]].append(kalshi_name)
            elif len(candidates) > 1:
                best_fit = candidates[0]
                best_ratio = fuzz_ratio(best_fit, kalshi_name)
                for name in candidates:
                    curr_ratio = fuzz_ratio(name, kalshi_name)
                    if curr_ratio > best_ratio:
                        best_fit = name
                        best_ratio = curr_ratio
                candidates_dict[best_fit].append(kalshi_name)

        for odd, kalsh in candidates_dict.items():
            best_fit = kalsh[0]
            best_ratio = fuzz_ratio(best_fit, odd)
            if len(kalsh) > 1:
                for name in kalsh:
                    curr_ratio = fuzz_ratio(name, odd)
                    if curr_ratio > best_ratio:
                        best_fit = name
                        best_ratio = curr_ratio
            matched_odds.append(odd)
            matched_kalshi.append(best_fit)
        return matched_kalshi, matched_odds

    matched_kalshi_h2h, matched_odds_h2h = fuzzy_match_kalshi_to_odds(
        kalshi_winners_teams,
        odds_teams_by_market.get("h2h", pd.Index([]))
    )

    matched_kalshi_spreads, matched_odds_spreads = fuzzy_match_kalshi_to_odds(
        kalshi_spreads_teams,
        odds_teams_by_market.get("spreads", pd.Index([]))
    )

    odds_winners_df = odds_winners_df[
        odds_winners_df["home_team"].isin(matched_odds_h2h) | odds_winners_df["away_team"].isin(matched_odds_h2h)
    ].drop_duplicates(subset="team").sort_values(by="home_team").reset_index(drop=True)

    kalshi_winners_df = kalshi_winners_df[
        kalshi_winners_df["home_team"].isin(matched_kalshi_h2h) | kalshi_winners_df["away_team"].isin(matched_kalshi_h2h)
    ].sort_values(by="home_team").reset_index(drop=True)

    odds_spreads_df = odds_spreads_df[odds_spreads_df["team"].isin(matched_odds_spreads)].reset_index(drop=True)
    kalshi_spreads_df = kalshi_spreads_df[kalshi_spreads_df["team"].isin(matched_kalshi_spreads)].reset_index(drop=True) if not kalshi_spreads_df.empty else kalshi_spreads_df

    kalshi_cols = ["ticker", "yes_bid", "yes_ask", "home_team", "away_team"]
    odds_cols = ["market", "start_time", "team", "home_team", "away_team", "avg_fair_prb"]

    kalshi_subset = kalshi_winners_df[kalshi_cols].rename(columns={
        "home_team": "kalshi_home_team",
        "away_team": "kalshi_away_team"
    })
    odds_subset = odds_winners_df[odds_cols].rename(columns={
        "home_team": "odds_home_team",
        "away_team": "odds_away_team"
    })

    combined_rows = []
    len_matched = min(len(matched_odds_h2h), len(matched_kalshi_h2h))
    for i in range(len_matched):
        odds_name = matched_odds_h2h[i]
        kalshi_name = matched_kalshi_h2h[i]
        odds_row = odds_subset.loc[odds_subset["team"] == odds_name]
        if odds_row.empty:
            continue
        kalshi_rows = kalshi_subset.loc[
            (kalshi_subset["kalshi_home_team"] == kalshi_name) | (kalshi_subset["kalshi_away_team"] == kalshi_name)
        ]
        if kalshi_rows.empty:
            continue
        k1 = kalshi_rows.iloc[0]
        if len(kalshi_rows) > 1:
            k2 = kalshi_rows.iloc[1]
        else:
            k2 = kalshi_rows.iloc[0]
        midprice1 = (k1["yes_bid"] + k1["yes_ask"]) / 2
        midprice2 = (k2["yes_bid"] + k2["yes_ask"]) / 2
        prb = odds_row["avg_fair_prb"].astype(float).item()
        combined_row = pd.concat([k1, odds_row.iloc[0]])
        if pd.notna(prb):
            if ((midprice1 - prb) ** 2) >= ((midprice2 - prb) ** 2):
                combined_row = pd.concat([k2, odds_row.iloc[0]])
        combined_rows.append(combined_row)

    combined_winners_df = pd.DataFrame(combined_rows).reset_index(drop=True)

    EDGE = CONFIG.DATA.EDGE_WINNERS
    KELLY_UPPERBOUND = CONFIG.DATA.KELLY_CAP
    BANKROLL = bankroll_winners
    Q1_WEIGHT = CONFIG.DATA.Q1_WEIGHT
    Q2_WEIGHT = CONFIG.DATA.Q2_WEIGHT
    Q3_WEIGHT = CONFIG.DATA.Q3_WEIGHT
    Q4_WEIGHT = CONFIG.DATA.Q4_WEIGHT

    edge_winners_df = combined_winners_df.loc[
        (combined_winners_df["avg_fair_prb"] >= combined_winners_df["yes_ask"] + EDGE) |
        (combined_winners_df["avg_fair_prb"] <= combined_winners_df["yes_bid"] - EDGE)
    ].reset_index(drop=True)

    if not edge_winners_df.empty:
        midprice = (edge_winners_df["yes_bid"] + edge_winners_df["yes_ask"]) / 2
        q = edge_winners_df["avg_fair_prb"]
        p = midprice

        edge_winners_df["raw_kelly"] = np.where(
            q > p,
            (q - p) / (1 - p),
            (p - q) / p
        )

        total_kelly = edge_winners_df["raw_kelly"].sum()
        if total_kelly:
            edge_winners_df["raw_kelly"] = pd.DataFrame({
                "original": edge_winners_df["raw_kelly"],
                "normalized": (edge_winners_df["raw_kelly"] / total_kelly)
            }).min(axis=1)

        def scale_kelly(row):
            k = row["raw_kelly"]
            p_val = row["avg_fair_prb"]
            if k == 0 or pd.isna(k):
                return 0
            if 0.05 <= p_val < 0.25:
                return min(Q1_WEIGHT * k, KELLY_UPPERBOUND)
            elif 0.25 <= p_val < 0.5:
                return min(Q2_WEIGHT * k, KELLY_UPPERBOUND)
            elif 0.5 <= p_val < 0.75:
                return min(Q3_WEIGHT * k, KELLY_UPPERBOUND)
            elif 0.75 <= p_val < 0.95:
                return min(Q4_WEIGHT * k, KELLY_UPPERBOUND)
            else:
                return 0

        edge_winners_df["real_kelly"] = edge_winners_df.apply(scale_kelly, axis=1)
        edge_winners_df["optimal_bet"] = edge_winners_df["real_kelly"] * BANKROLL

        num_contracts = edge_winners_df["optimal_bet"] // edge_winners_df["yes_bid"]
        edge_winners_df["num_contracts"] = num_contracts
        trading_cost = np.ceil(100 * (0.0175 * num_contracts * edge_winners_df["yes_bid"] * (1 - edge_winners_df["yes_bid"]))) / 100
        edge_winners_df["trading_cost"] = trading_cost
        profit = (1 - edge_winners_df["yes_bid"]) * num_contracts - trading_cost
        edge_winners_df["profit"] = profit
        edge_winners_df["ev"] = (profit * edge_winners_df["avg_fair_prb"] - edge_winners_df["optimal_bet"] * (1 - edge_winners_df["avg_fair_prb"])).round(2)
        filtered_winners_df = edge_winners_df.loc[edge_winners_df["ev"] > CONFIG.DATA.WINNERS_EV_THRESHOLD].reset_index(drop=True)
    else:
        filtered_winners_df = pd.DataFrame()

    filtered_spreads_df = pd.DataFrame()
    if not kalshi_spreads_df.empty and not odds_spreads_df.empty:
        kalshi_cols = ["ticker", "yes_bid", "yes_ask", "team", "points"]
        odds_cols = ["market", "start_time", "team", "home_team", "away_team", "avg_fair_prb", "point"]

        odds_subset = odds_spreads_df[odds_cols].rename(columns={
            "home_team": "odds_home_team",
            "away_team": "odds_away_team",
            "team": "odds_team"
        })

        kalshi_subset = kalshi_spreads_df[kalshi_cols]
        combined_rows = []

        for _, kalshi_row in kalshi_subset.iterrows():
            kalshi_home = kalshi_row["team"]
            for _, odds_row in odds_subset.iterrows():
                odds_home = odds_row["odds_team"]
                if isinstance(kalshi_home, str) and isinstance(odds_home, str) and (kalshi_home in odds_home) and (kalshi_row["points"] == odds_row["point"]):
                    combined_row = pd.concat([kalshi_row, odds_row])
                    combined_rows.append(combined_row)

        combined_spreads_df = pd.DataFrame(combined_rows).drop_duplicates(subset="ticker").reset_index(drop=True)

        EDGE = CONFIG.DATA.EDGE_SPREADS
        BANKROLL = bankroll_spreads

        edge_spreads_df = combined_spreads_df.loc[
            (combined_spreads_df["avg_fair_prb"] >= combined_spreads_df["yes_ask"] + EDGE) |
            (combined_spreads_df["avg_fair_prb"] <= combined_spreads_df["yes_bid"] - EDGE)
        ].reset_index(drop=True)

        if not edge_spreads_df.empty:
            midprice = (edge_spreads_df["yes_bid"] + edge_spreads_df["yes_ask"]) / 2
            q = edge_spreads_df["avg_fair_prb"]
            p = midprice

            edge_spreads_df["raw_kelly"] = np.where(
                q > p,
                (q - p) / (1 - p),
                (p - q) / p
            )

            total_kelly = edge_spreads_df["raw_kelly"].sum()
            if total_kelly:
                edge_spreads_df["raw_kelly"] = pd.DataFrame({
                    "original": edge_spreads_df["raw_kelly"],
                    "normalized": (edge_spreads_df["raw_kelly"] / total_kelly)
                }).min(axis=1)

            def scale_kelly_spreads(row):
                k = row["raw_kelly"]
                p_val = row["avg_fair_prb"]
                if k == 0 or pd.isna(k):
                    return 0
                if 0.1 <= p_val < 0.25:
                    return min(Q1_WEIGHT * k, KELLY_UPPERBOUND)
                elif 0.25 <= p_val < 0.5:
                    return min(Q2_WEIGHT * k, KELLY_UPPERBOUND)
                elif 0.5 <= p_val < 0.75:
                    return min(Q3_WEIGHT * k, KELLY_UPPERBOUND)
                elif 0.75 <= p_val < 0.9:
                    return min(Q4_WEIGHT * k, KELLY_UPPERBOUND)
                else:
                    return 0

            edge_spreads_df["real_kelly"] = edge_spreads_df.apply(scale_kelly_spreads, axis=1)
            edge_spreads_df["optimal_bet"] = edge_spreads_df["real_kelly"] * BANKROLL

            num_contracts = edge_spreads_df["optimal_bet"] // edge_spreads_df["yes_bid"]
            edge_spreads_df["num_contracts"] = num_contracts
            trading_cost = np.ceil(100 * (0.0175 * num_contracts * edge_spreads_df["yes_bid"] * (1 - edge_spreads_df["yes_bid"]))) / 100
            edge_spreads_df["trading_cost"] = trading_cost
            profit = (1 - edge_spreads_df["yes_bid"]) * num_contracts - trading_cost
            edge_spreads_df["profit"] = profit
            edge_spreads_df["ev"] = (profit * edge_spreads_df["avg_fair_prb"] - edge_spreads_df["optimal_bet"] * (1 - edge_spreads_df["avg_fair_prb"])).round(2)
            filtered_spreads_df = edge_spreads_df.loc[edge_spreads_df["ev"] > CONFIG.DATA.SPREADS_EV_THRESHOLD].reset_index(drop=True)

    return filtered_winners_df, filtered_spreads_df


def _summarize_portfolio(df) -> Dict[str, float]:
    if df is None or df.empty:
        return {"max_loss": 0.0, "max_profit": 0.0, "portfolio_ev": 0.0, "count": 0}
    total_loss = float(df["optimal_bet"].sum())
    total_profit = float(df["profit"].sum())
    total_ev = float(df["ev"].sum())
    return {
        "max_loss": -total_loss,
        "max_profit": total_profit,
        "portfolio_ev": total_ev,
        "count": int(len(df)),
    }


def build_analysis_signals(date_override: Optional[str], bankroll_winners: float, bankroll_spreads: float) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, float]], Dict[str, str]]:
    """
    Rebuild filtered frames, write them to CSVs, and return signals + summaries + csv paths.
    """
    winners_df, spreads_df = build_filtered_frames(date_override, bankroll_winners, bankroll_spreads)
    summaries = {
        "winners": _summarize_portfolio(winners_df),
        "spreads": _summarize_portfolio(spreads_df),
    }

    csv_paths: Dict[str, str] = {}
    signals: Dict[str, Dict[str, Any]] = {}

    def _safe_count(val: Any) -> int:
        try:
            return int(float(val))
        except Exception:
            return 0

    def direction_for_row(row, edge):
        return "buy_yes" if row["avg_fair_prb"] >= row["yes_ask"] + edge else "sell_yes"

    def write_csv(label: str, df):
        if df is None:
            return
        out_dir = REPO_ROOT / CONFIG.DATA.OUTPUT_DIR
        out_dir.mkdir(parents=True, exist_ok=True)
        date_str = date_override or CONFIG.DATA.DATE or now_utc().date().isoformat()
        path = out_dir / f"filtered_{label}_{date_str}.csv"
        file_exists = path.exists()
        with open(path, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(df.columns))
            if not file_exists:
                writer.writeheader()
            for _, row in df.iterrows():
                row_dict = {col: row[col] for col in df.columns}
                writer.writerow(row_dict)
                ticker = row_dict.get("ticker")
                if not isinstance(ticker, str) or not ticker:
                    continue
                edge_use = CONFIG.DATA.EDGE_WINNERS if label == "winners" else CONFIG.DATA.EDGE_SPREADS
                signals[ticker] = {
                    "source": label.rstrip("s"),
                    "direction": direction_for_row(row, edge_use),
                    "fair_prob": float(row["avg_fair_prb"]),
                    "edge": edge_use,
                    "ev": float(row["ev"]),
                    "num_contracts": _safe_count(row.get("num_contracts")),
                    "row": row_dict,
                }
        csv_paths[label] = str(path)

    write_csv("winners", winners_df)
    write_csv("spreads", spreads_df)

    if not signals:
        log_health("analysis_no_signals", date=date_override or CONFIG.DATA.DATE)
    return signals, summaries, csv_paths



# =========================
# ======= STRATEGY  =======
# =========================

def idem_key(intent: Dict[str,Any]) -> str:
    bucket = int(time.time() // 5)  # 5-second bucket
    base = f"{intent['ticker']}|{intent['side']}|{intent['price']}|{intent['count']}|{bucket}"
    return hashlib.sha256(base.encode()).hexdigest()

_seen_statuses = set()
def note_status_once(status: str):
    status = (status or "").lower()
    global _seen_statuses
    if status not in _seen_statuses:
        _seen_statuses.add(status)
        log_health("status_seen", status=status)

def decide_intents(ticker: str, market: dict, orderbook: dict) -> List[Dict[str,Any]]:
    """
    Data-driven strategy that only trades tickers surfaced by the notebook
    analysis (filtered_winners_df / filtered_spreads_df). The live orderbook is
    checked to ensure the edge still exists before sending the sized order.
    """
    intents: List[Dict[str,Any]] = []

    market = normalize_market(market)
    status = (market.get("status") or "").lower()
    note_status_once(status)
    if CONFIG.ALLOWED_STATUSES is not None and status not in CONFIG.ALLOWED_STATUSES:
        if CONFIG.DEBUG_SKIPS:
            log_health("skip_status", ticker=ticker, status=status)
        return intents

    # Skip games that have already started.
    event_start = parse_event_start(market)
    if event_start and now_utc() >= event_start:
        if CONFIG.DEBUG_SKIPS:
            log_health("skip_already_started", ticker=ticker, start=event_start.isoformat())
        return intents

    # robust BBO
    bbo = best_prices_from_sources(market, orderbook)
    yes_bid, yes_ask, no_bid, no_ask = bbo["yes_bid"], bbo["yes_ask"], bbo["no_bid"], bbo["no_ask"]

    signal = ANALYSIS_SIGNALS.get(ticker)
    if not signal:
        if CONFIG.DEBUG_SKIPS:
            log_health("skip_no_analysis_signal", ticker=ticker)
        return intents

    if yes_bid is None and yes_ask is None:
        if CONFIG.DEBUG_SKIPS:
            ob = (orderbook or {}).get("orderbook") or {}
            log_health("skip_no_bbo", ticker=ticker, ob_keys=",".join(sorted(ob.keys())))
        return intents

    fair_yes = signal.get("fair_prob")
    direction = signal.get("direction")
    edge_threshold = signal.get("edge", 0)
    yes_levels, _ = parse_ob_yes_no(orderbook)
    best_yes_ask_qty = yes_levels[0][1] if yes_levels else None

    base_count = signal.get("num_contracts") or 0
    try:
        base_count = int(base_count)
    except Exception:
        base_count = 0
    if base_count <= 0:
        if CONFIG.DEBUG_SKIPS:
            log_health("skip_non_positive_size", ticker=ticker, size=base_count)
        return intents

    spread = round(yes_ask - yes_bid, 4) if yes_bid is not None and yes_ask is not None else None

    if direction == "buy_yes":
        if yes_ask is None:
            if CONFIG.DEBUG_SKIPS:
                log_health("skip_no_ask", ticker=ticker)
        else:
            edge_now = fair_yes - yes_ask if fair_yes is not None else None
            if edge_now is not None and edge_now >= edge_threshold:
                count = base_count
                if best_yes_ask_qty:
                    count = min(count, best_yes_ask_qty)
                if count > 0:
                    intents.append({
                        "intent_id": str(uuid.uuid4()),
                        "ticker": ticker,
                        "side": "yes",
                        "action": "buy",
                        "price": clamp_price(yes_ask),
                        "count": count,
                        "reason": f"analysis_buy_{signal.get('source')}",
                        "expires_at": event_start.isoformat() if event_start else None
                    })
            else:
                if CONFIG.DEBUG_SKIPS:
                    log_health("skip_edge_not_met", ticker=ticker, edge=edge_now, threshold=edge_threshold)
    elif direction == "sell_yes":
        if yes_bid is None:
            if CONFIG.DEBUG_SKIPS:
                log_health("skip_no_bid", ticker=ticker)
        else:
            edge_now = yes_bid - fair_yes if fair_yes is not None else None
            if edge_now is not None and edge_now >= edge_threshold:
                count = base_count
                intents.append({
                    "intent_id": str(uuid.uuid4()),
                    "ticker": ticker,
                    "side": "yes",
                    "action": "sell",
                    "price": clamp_price(yes_bid),
                    "count": count,
                    "reason": f"analysis_sell_{signal.get('source')}",
                    "expires_at": event_start.isoformat() if event_start else None
                })
            else:
                if CONFIG.DEBUG_SKIPS:
                    log_health("skip_edge_not_met", ticker=ticker, edge=edge_now, threshold=edge_threshold)

    for it in intents:
        log_decision(ticker, it, {
            "fair_yes": fair_yes,
            "edge": edge_threshold,
            "spread": spread,
            "yes_bid": yes_bid, "yes_ask": yes_ask, "no_bid": no_bid, "no_ask": no_ask
        })

    return intents


# =========================
# ========= MAIN ==========
# =========================

RUN = True
ORDERS_SENT = 0
def _handle_sig(sig, frame):
    global RUN
    RUN = False
    print("🛑 Signal received; shutting down...")

signal.signal(signal.SIGINT, _handle_sig)
signal.signal(signal.SIGTERM, _handle_sig)

def bootstrap_readiness():
    test = list_markets(status=None)
    if not test:
        sys.exit("❌ Could not reach markets listing. Check CONFIG.HOST/API_KEY or network.")
    log_health("ready", dry_run=CONFIG.DRY_RUN, host=CONFIG.HOST,
               series=",".join(CONFIG.SERIES), tickers=",".join(CONFIG.TICKERS))

def submit_intent(intent: Dict[str,Any]):
    price = clamp_price(float(intent["price"]))
    if price is None or price < CONFIG.MIN_PRICE or price > CONFIG.MAX_PRICE:
        return
    count = int(intent["count"])
    if count <= 0:
        return
    payload = {
        "ticker": intent["ticker"],
        "side": intent["side"],          # "yes" or "no"
        "action": intent.get("action", "buy"),  # required by API
        "type": "limit",
        "count": count,
        "time_in_force": "good_til_cancel",
        "client_order_id": intent.get("intent_id"),
    }
    exp = intent.get("expires_at")
    if exp:
        try:
            # Kalshi expects milliseconds since epoch for expiration_time
            exp_dt = datetime.fromisoformat(exp)
            payload["expiration_time"] = int(exp_dt.timestamp() * 1000)
        except Exception:
            pass
    # Kalshi expects exactly one price field; send cents.
    price_cents = int(round(price * 100))
    if payload["side"] == "yes":
        payload["yes_price"] = price_cents
    else:
        payload["no_price"] = price_cents

    resp = place_order(payload, idem_key(intent))
    log_order("place", payload, resp)
    success = False
    if resp and (resp.get("dry_run") or resp.get("ok") or resp.get("order")):
        success = True
    if success:
        chosen_odds = price if payload["side"] == "yes" else max(CONFIG.MIN_PRICE, min(CONFIG.MAX_PRICE, round(1.0 - price, 4)))
        print(f"🪙 {mode} order: {payload['ticker']} side={payload['side']} action={payload['action']} size={count} price={price:.2f} odds={chosen_odds:.2f}")
    else:
        print(f"❌ order failed for {payload['ticker']} side={payload['side']} size={count} price={price}")
    # Stop if an order is immediately filled or after 5 placement attempts.
    global RUN, ORDERS_SENT
    ORDERS_SENT += 1
    if resp:
        order_obj = resp.get("order") if isinstance(resp, dict) else None
        status = (order_obj or {}).get("status")
        err_details = None
        if isinstance(resp, dict) and "error" in resp:
            err_details = resp["error"].get("details") or resp["error"].get("message")
        if err_details and "TimeInForce" in str(err_details):
            print("🛑 Fatal TIF validation error; shutting down to avoid repeats.")
            RUN = False
        if status == "filled" or ORDERS_SENT >= 5:
            RUN = False
    elif ORDERS_SENT >= 5:
        RUN = False

def parse_cli_args():
    parser = argparse.ArgumentParser(description="Kalshi live trader")
    parser.add_argument(
        "--live",
        action="store_true",
        help="Send real orders (disable DRY_RUN). Without this flag, orders are simulated.",
    )
    parser.add_argument(
        "--api-key",
        dest="api_key",
        help="Override CONFIG.API_KEY or use env KALSHI_API_KEY to avoid hardcoding.",
    )
    parser.add_argument(
        "--host",
        dest="host",
        help="Override CONFIG.HOST or use env KALSHI_HOST to select a different API host.",
    )
    parser.add_argument(
        "--auth-mode",
        dest="auth_mode",
        choices=["bearer", "x_api_key"],
        help="Override CONFIG.AUTH_MODE or use env KALSHI_AUTH_MODE.",
    )
    return parser.parse_args()

def select_working_host(preferred: Optional[str], require_order_host: bool) -> None:
    """
    Try host candidates until a markets call succeeds.
    """
    tried = []
    candidates = []
    if preferred:
        candidates.append(preferred)
    candidates.append(CONFIG.HOST)
    candidates.extend(getattr(CONFIG, "HOST_FALLBACKS", []))
    seen = set()
    candidates = [h for h in candidates if not (h in seen or seen.add(h))]

    for host in candidates:
        CONFIG.HOST = host
        tried.append(host)
        payload = list_markets(status=None)
        if payload and payload.get("markets"):
            print(f"🌐 Using host: {host}")
            return
    print(f"⚠️ Tried hosts with no success: {', '.join(tried)}")
    sys.exit("❌ Could not reach an order-capable host. Check DNS/VPN/host override.")

def main():
    args = parse_cli_args()

    # Allow host override via CLI/env for environments where default hostname does not resolve.
    env_host = os.getenv("KALSHI_HOST")
    if args.host:
        CONFIG.HOST = args.host
    elif env_host:
        CONFIG.HOST = env_host

    # Allow environment/CLI overrides for API key so the script can actually place orders.
    env_key = os.getenv("KALSHI_API_KEY")
    if args.api_key:
        CONFIG.API_KEY = args.api_key
    elif env_key:
        CONFIG.API_KEY = env_key

    # Allow auth mode override via CLI/env.
    env_auth = os.getenv("KALSHI_AUTH_MODE")
    if args.auth_mode:
        CONFIG.AUTH_MODE = args.auth_mode
    elif env_auth:
        CONFIG.AUTH_MODE = env_auth

    # Debug auth/host inputs (avoid printing secrets).
    key_id_preview = (CONFIG.API_KEY or "")[:6] + "…" if CONFIG.API_KEY else "None"
    pem_path = os.getenv("KALSHI_PRIVATE_KEY_PATH") or CONFIG.PRIVATE_KEY_PATH
    pem_exists = os.path.exists(os.path.expanduser(pem_path)) if pem_path else False
    print(f"🔧 Host={CONFIG.HOST}  AuthMode={CONFIG.AUTH_MODE}  KeyId(prefix)={key_id_preview}  PEM={'yes' if pem_exists else 'missing'}")

    # Pick a reachable host before proceeding (skip read-only host when live).
    select_working_host(args.host or env_host, require_order_host=not CONFIG.DRY_RUN)

    # Live toggle: default is live unless user explicitly keeps dry-run.
    if args.live:
        CONFIG.DRY_RUN = False
    if CONFIG.DRY_RUN:
        print("🧪 DRY-RUN mode: orders will be echoed locally and not sent.")
    else:
        print("🚨 LIVE mode: orders will be sent to Kalshi.")

    if not CONFIG.API_KEY or CONFIG.API_KEY == "PUT_YOUR_KALSHI_API_KEY_HERE":
        sys.exit("❌ Please paste your real API key into CONFIG.API_KEY at the top of this file.")

    bootstrap_readiness()

    # Bankroll allocation
    total_bankroll = CONFIG.DATA.TOTAL_BANKROLL
    if total_bankroll is None:
        total_bankroll = fetch_total_bankroll()
        if total_bankroll is None:
            sys.exit("❌ Could not fetch total bankroll from account and no override provided.")
    winners_bankroll = total_bankroll * CONFIG.DATA.WINNERS_PROPORTION
    spreads_bankroll = total_bankroll * CONFIG.DATA.SPREADS_PROPORTION

    # Build analysis outputs and CSVs (filtered_winners / filtered_spreads).
    global ANALYSIS_SIGNALS, ANALYSIS_SUMMARY, ANALYSIS_CSVS
    ANALYSIS_SIGNALS, ANALYSIS_SUMMARY, ANALYSIS_CSVS = build_analysis_signals(CONFIG.DATA.DATE, winners_bankroll, spreads_bankroll)
    target_tickers = set(ANALYSIS_SIGNALS.keys())
    all_tickers = list(target_tickers)

    def _print_summary(label: str, summary: Dict[str, float]):
        print(f"{label} summary:")
        print(f"  Count: {summary.get('count', 0)}")
        print(f"  Max Loss: {summary.get('max_loss', 0.0):.2f}")
        print(f"  Max Profit: {summary.get('max_profit', 0.0):.2f}")
        print(f"  Portfolio EV: {summary.get('portfolio_ev', 0.0):.2f}")

    _print_summary("Winners", ANALYSIS_SUMMARY.get("winners", {}))
    _print_summary("Spreads", ANALYSIS_SUMMARY.get("spreads", {}))

    if not target_tickers:
        print("⚠️ No analysis signals were built; trader will not place orders.")
        return

    print(f"🎯 Trading on {len(all_tickers)} tickers from analysis signals.")
    if ANALYSIS_CSVS:
        for label, path in ANALYSIS_CSVS.items():
            print(f"🗒️ {label} CSV written to: {path}")

    # Single pass: place trades from the analysis signals and exit.
    for t in all_tickers:
        try:
            mkt = get_market(t) or {}
            ob = get_orderbook(t) or {}
            intents = decide_intents(t, mkt, ob)
            for it in intents:
                submit_intent(it)
        except Exception as e:
            log_health("decision_error", ticker=t, error=str(e))
            continue

    log_health("stopped")


if __name__ == "__main__":
    main()
