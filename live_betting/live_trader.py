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
    HOST = "https://api.elections.kalshi.com"    # elections environment
    API_KEY = "e8b43912-6603-413c-b544-3ca7f47cd06b"      # <<< REQUIRED: paste your key here
    AUTH_MODE = "bearer"                          # "bearer" or "x_api_key"

    # --- Discovery scope ---
    SERIES = ["KXNFLGAME", "KXNBAGAME", "KXNCAAFGAME", "KXNCAAMBGAME", "KXTENNISMATCH", 
    "KXEPLGAME", "KXUELGAME", "KXNFLTOTALS", "KXNFLSPREADS", "KXNBATOTALS",]   # add/remove series as desired
    TICKERS = []                                       # leave empty to use discovery from SERIES
    MAX_DISCOVERY_PAGES = 20
    DISCOVERY_SAMPLE_ROWS = 10

    # Client-side status allowlist — set to None to accept ANY status
    ALLOWED_STATUSES = None   # e.g., {"active","open","trading","open_for_trading"}  or None

    # --- Behavior toggles ---
    DRY_RUN = True               # safe by default
    POLL_INTERVAL_SEC = 4        # seconds between loops
    REQ_TIMEOUT = 15             # HTTP timeout
    MAX_RETRIES = 3
    RETRY_SLEEP = 1.0            # backoff base seconds
    TICKERS_PER_LOOP = 60        # process at most this many tickers per loop (for visibility)

    # --- Trading policy & risk (very permissive for observation) ---
    TIME_IN_FORCE = "GTC"        # "GTC" or "DAY"
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
    HEARTBEAT_PRINT = True         # print per loop summary to console
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

def _headers(extra: Optional[Dict[str,str]] = None) -> Dict[str,str]:
    if not CONFIG.API_KEY or CONFIG.API_KEY == "PUT_YOUR_KALSHI_API_KEY_HERE":
        sys.exit("❌ Please paste your real API key into CONFIG.API_KEY at the top of this file.")
    h = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "kalshi-live-trader-observed/1.0",
    }
    if CONFIG.AUTH_MODE.lower() == "bearer":
        h["Authorization"] = f"Bearer {CONFIG.API_KEY}"
    else:
        h["X-API-Key"] = CONFIG.API_KEY
    if extra:
        h.update(extra)
    return h

def http_request(method: str, path: str, params: Optional[dict] = None,
                 body: Optional[dict] = None, extra_headers: Optional[dict] = None) -> Optional[dict]:
    url = f"{CONFIG.HOST.rstrip('/')}/trade-api/v2/{path.lstrip('/')}"
    data = json.dumps(body) if body is not None else None

    for attempt in range(1, CONFIG.MAX_RETRIES + 1):
        try:
            if CONFIG.DRY_RUN and method.upper() in {"POST","DELETE"}:
                # emulate success
                return {"dry_run": True, "echo": {"path": path, "params": params, "body": body}}
            r = requests.request(method.upper(), url, headers=_headers(extra_headers),
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
    return http_request("POST", "orders", body=payload, extra_headers={"Idempotency-Key": idem_key})


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
        # not returning here; keep going to still post makers for visibility if configured
        pass

    yes_levels, no_levels = parse_ob_yes_no(orderbook)
    best_yes_ask_qty = yes_levels[0][1] if yes_levels else 0
    best_no_ask_qty  = no_levels[0][1] if no_levels else 0

    # fair selector
    if CONFIG.STRAT.FAIR_MODE == "mid":
        fair = round((yes_bid + yes_ask)/2.0, 4)
    else:
        fair = 0.50

    buy_edge  = round(fair - yes_ask, 4)
    sell_edge = round(yes_bid - fair, 4)

    # BUY YES
    if buy_edge >= CONFIG.STRAT.BUY_EDGE:
        if buy_edge >= CONFIG.STRAT.CROSS_EDGE and best_yes_ask_qty >= 1:
            price = clamp_price(yes_ask)
            if price is not None:
                intents.append({
                    "intent_id": str(uuid.uuid4()),
                    "ticker": ticker, "side": "yes",
                    "price": price,
                    "count": min(CONFIG.STRAT.SIZE, max(1, best_yes_ask_qty)),
                    "reason": f"buy_yes_cross edge={buy_edge}"
                })
        else:
            # maker at yes_bid (join bid)
            maker_price = clamp_price(yes_bid)
            if maker_price is not None:
                intents.append({
                    "intent_id": str(uuid.uuid4()),
                    "ticker": ticker, "side": "yes",
                    "price": maker_price,
                    "count": CONFIG.STRAT.SIZE,
                    "reason": f"buy_yes_maker edge={buy_edge}"
                })

    # SELL YES (place NO)
    if sell_edge >= CONFIG.STRAT.SELL_EDGE:
        if sell_edge >= CONFIG.STRAT.CROSS_EDGE:
            price_no = clamp_price(1.0 - yes_bid)
            if no_ask is not None and price_no < no_ask - 2*CONFIG.MIN_TICK:
                if CONFIG.DEBUG_SKIPS:
                    log_health("skip_parity_cross", ticker=ticker, price_no=price_no, no_ask=no_ask)
            else:
                intents.append({
                    "intent_id": str(uuid.uuid4()),
                    "ticker": ticker, "side": "no",
                    "price": price_no,
                    "count": CONFIG.STRAT.SIZE if best_no_ask_qty <= 0 else min(CONFIG.STRAT.SIZE, max(1, best_no_ask_qty)),
                    "reason": f"sell_yes_cross edge={sell_edge}"
                })
        else:
            maker_no = clamp_price(1.0 - yes_ask)
            intents.append({
                "intent_id": str(uuid.uuid4()),
                "ticker": ticker, "side": "no",
                "price": maker_no,
                "count": CONFIG.STRAT.SIZE,
                "reason": f"sell_yes_maker edge={sell_edge}"
            })

    # If no edge intent, optionally post tiny makers to ensure visibility
    if not intents and CONFIG.STRAT.ALWAYS_POST_MAKER:
        maker_yes = clamp_price(yes_bid)
        maker_no  = clamp_price(1.0 - yes_ask)
        if maker_yes is not None:
            intents.append({
                "intent_id": str(uuid.uuid4()),
                "ticker": ticker, "side": "yes",
                "price": maker_yes,
                "count": 1,
                "reason": "maker_heartbeat_yes"
            })
        if maker_no is not None:
            intents.append({
                "intent_id": str(uuid.uuid4()),
                "ticker": ticker, "side": "no",
                "price": maker_no,
                "count": 1,
                "reason": "maker_heartbeat_no"
            })

    # decision log
    for it in intents:
        log_decision(ticker, it, {
            "fair_yes": fair,
            "edge": buy_edge if it["side"] == "yes" else sell_edge,
            "spread": spread,
            "yes_bid": yes_bid, "yes_ask": yes_ask, "no_bid": no_bid, "no_ask": no_ask
        })

    return intents


# =========================
# ========= MAIN ==========
# =========================

RUN = True
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
        "type": "limit",
        "price": price,
        "count": count,
        "time_in_force": CONFIG.TIME_IN_FORCE,
        "client_order_id": intent.get("intent_id"),
    }
    resp = place_order(payload, idem_key(intent))
    log_order("place", payload, resp)

def main():
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
        print(f"🧭 Discovered {len(all_tickers)} market(s). First 5: {all_tickers[:5]}")

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
                total_intents += len(intents)
            except Exception as e:
                log_health("decision_error", ticker=t, error=str(e))
                continue

        elapsed = (now_utc() - loop_started).total_seconds()
        if CONFIG.HEARTBEAT_PRINT:
            print(f"⏱ processed {len(batch)}/{len(all_tickers)} tickers, intents={total_intents} @ {now_iso()} (loop {elapsed:.2f}s)")
        time.sleep(max(0.0, CONFIG.POLL_INTERVAL_SEC - elapsed))

    log_health("stopped")


if __name__ == "__main__":
    main()
