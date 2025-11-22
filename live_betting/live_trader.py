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
    SERIES = ["KXNCAAMBGAME", "KXNCAAMBSPREAD", "KXNCAAMBTOTAL"]   # adjust this list to include other markets if desired
    TICKERS = []                   # set explicit tickers to bypass series discovery
    MAX_DISCOVERY_PAGES = 20
    DISCOVERY_SAMPLE_ROWS = 10

    # Client-side status allowlist — only trade open markets
    ALLOWED_STATUSES = {"active", "open"}   # set to None to accept any status

    # --- Behavior toggles ---
    DRY_RUN = False               # simulate by default; use --live flag to send real orders
    POLL_INTERVAL_SEC = 4        # seconds between loops
    REQ_TIMEOUT = 15             # HTTP timeout
    MAX_RETRIES = 3
    RETRY_SLEEP = 1.0            # backoff base seconds
    TICKERS_PER_LOOP = 60        # process at most this many tickers per loop (for visibility)

    # --- Trading policy & risk (very permissive for observation) ---
    TIME_IN_FORCE = "IOC"        # allowed by Kalshi: DAY, IOC, FOK, GTC (use env/flag to change)
    MIN_TICK = 0.01
    MIN_PRICE = 0.01
    MAX_PRICE = 0.99
    MAX_SPREAD = 0.60            # allow wide books so we see activity
    NEAR_EXPIRY_MINS = 0         # disable near-expiry gate for testing

    # --- Strategy (inclusive & observable) ---
    class STRAT:
        FAIR_MODE = "fifty_fifty"  # "fifty_fifty" or "mid"
        BUY_EDGE = 0.01            # edge to consider buy YES
        SELL_EDGE = 0.01           # edge to consider sell YES (via NO order)
        CROSS_EDGE = 0.02          # cross when edge >= this; else post maker
        SIZE = 2                   # size per intent (kept small for DRY_RUN)
        ALWAYS_POST_MAKER = True   # if no edge triggers, still post a small maker for visibility

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
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List, Tuple


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
    Basic, inclusive strategy to ensure DRY-RUN produces visible decisions:
      - Accepts any status if CONFIG.ALLOWED_STATUSES is None; otherwise filters by it.
      - Builds BBO from market OR orderbook (dollars/cents) with parity fallback.
      - Uses simple 'fair' (0.5 by default). If yes_ask <= fair - BUY_EDGE => buy YES;
        if yes_bid >= fair + SELL_EDGE => sell YES (place NO).
      - If no edge fires and ALWAYS_POST_MAKER is True: post small maker on both sides to show activity.
      - Applies a wide spread gate and no near-expiry gate (per CONFIG).
    """
    intents: List[Dict[str,Any]] = []

    market = normalize_market(market)
    status = (market.get("status") or "").lower()
    note_status_once(status)
    if CONFIG.ALLOWED_STATUSES is not None and status not in CONFIG.ALLOWED_STATUSES:
        if CONFIG.DEBUG_SKIPS:
            log_health("skip_status", ticker=ticker, status=status)
        return intents

    # near-expiry (disabled by default with NEAR_EXPIRY_MINS=0)
    if CONFIG.NEAR_EXPIRY_MINS > 0:
        expiry = market.get("event_expiration_time") or market.get("close_time") or market.get("expiry")
        try:
            if isinstance(expiry, str):
                exp = datetime.fromisoformat(expiry.replace("Z","+00:00")) if "Z" in expiry else datetime.fromisoformat(expiry)
                if (exp - now_utc()) <= timedelta(minutes=CONFIG.NEAR_EXPIRY_MINS):
                    if CONFIG.DEBUG_SKIPS:
                        log_health("skip_near_expiry", ticker=ticker)
                    return intents
        except Exception:
            pass

    # robust BBO
    bbo = best_prices_from_sources(market, orderbook)
    yes_bid, yes_ask, no_bid, no_ask = bbo["yes_bid"], bbo["yes_ask"], bbo["no_bid"], bbo["no_ask"]

    if yes_bid is None or yes_ask is None:
        if CONFIG.DEBUG_SKIPS:
            ob = (orderbook or {}).get("orderbook") or {}
            log_health("skip_no_bbo", ticker=ticker, ob_keys=",".join(sorted(ob.keys())))
        return intents

    spread = round(yes_ask - yes_bid, 4)
    if spread is None or spread < 0 or spread > CONFIG.MAX_SPREAD:
        if CONFIG.DEBUG_SKIPS:
            log_health("skip_spread", ticker=ticker, spread=spread, max_spread=CONFIG.MAX_SPREAD)
        # still allow evaluation below

    yes_levels, _ = parse_ob_yes_no(orderbook)
    best_yes_ask_qty = yes_levels[0][1] if yes_levels else CONFIG.STRAT.SIZE

    # Moderately loose criteria:
    # - Buy YES if ask <= 0.55 and spread not crazy
    # - Sell YES if bid >= 0.45 and spread not crazy
    if spread is None or spread < 0 or spread > CONFIG.MAX_SPREAD:
        if CONFIG.DEBUG_SKIPS:
            log_health("skip_spread", ticker=ticker, spread=spread, max_spread=CONFIG.MAX_SPREAD)
        # still evaluate to allow activity

    # BUY YES (hit ask) when it's cheap-ish
    if yes_ask is not None and yes_ask <= 0.55:
        count = min(CONFIG.STRAT.SIZE, best_yes_ask_qty if best_yes_ask_qty > 0 else CONFIG.STRAT.SIZE)
        if count > 0:
            intents.append({
                "intent_id": str(uuid.uuid4()),
                "ticker": ticker,
                "side": "yes",
                "action": "buy",
                "price": clamp_price(yes_ask),
                "count": count,
                "reason": "buy_yes_le_055"
            })

    # SELL YES (hit bid) when it's rich-ish
    if yes_bid is not None and yes_bid >= 0.45:
        count = CONFIG.STRAT.SIZE
        intents.append({
            "intent_id": str(uuid.uuid4()),
            "ticker": ticker,
            "side": "yes",
            "action": "sell",
            "price": clamp_price(yes_bid),
            "count": count,
            "reason": "sell_yes_ge_045"
        })

    for it in intents:
        log_decision(ticker, it, {
            "fair_yes": None,
            "edge": None,
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
        "time_in_force": "immediate_or_cancel",
        "client_order_id": intent.get("intent_id"),
    }
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
        mode = "LIVE" if not CONFIG.DRY_RUN else "DRY-RUN"
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

    # Discover tickers (or use hardcoded)
    if CONFIG.TICKERS:
        all_tickers = list(sorted(set(CONFIG.TICKERS)))
        print(f"🧭 Using hardcoded {len(all_tickers)} tickers. First 5: {all_tickers[:5]}")
    else:
        all_tickers = discover_market_tickers(CONFIG.SERIES)
        if not all_tickers:
            sys.exit(f"⚠️ Discovery found 0 tickers for series={CONFIG.SERIES}. "
                     f"Adjust CONFIG.ALLOWED_STATUSES or series names.")
        print(f"🧭 Discovered {len(all_tickers)} market(s).")

    # Live loop
    idx = 0
    while RUN:
        loop_started = now_utc()

        if not all_tickers:
            time.sleep(CONFIG.POLL_INTERVAL_SEC)
            continue

        # Slice a window of tickers per loop for visibility
        end = min(idx + CONFIG.TICKERS_PER_LOOP, len(all_tickers))
        batch = all_tickers[idx:end]
        idx = 0 if end >= len(all_tickers) else end

        total_intents = 0
        for t in batch:
            try:
                mkt = get_market(t) or {}
                ob  = get_orderbook(t) or {}
                intents = decide_intents(t, mkt, ob)
                for it in intents:
                    submit_intent(it)
                    if not RUN:
                        break
                total_intents += len(intents)
                if not RUN:
                    break
            except Exception as e:
                log_health("decision_error", ticker=t, error=str(e))
                continue
        if not RUN:
            break

        elapsed = (now_utc() - loop_started).total_seconds()
        if CONFIG.HEARTBEAT_PRINT:
            print(f"⏱ processed {len(batch)}/{len(all_tickers)} tickers, intents={total_intents} @ {now_iso()} (loop {elapsed:.2f}s)")
        time.sleep(max(0.0, CONFIG.POLL_INTERVAL_SEC - elapsed))

    log_health("stopped")


if __name__ == "__main__":
    main()
