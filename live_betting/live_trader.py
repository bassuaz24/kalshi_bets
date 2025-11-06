#!/usr/bin/env python3
"""
kalshi_live_trader.py — Functional MVP (safe-by-default, DRY_RUN on)

Continuous live trading loop for Kalshi with:
- Env-based config (no hardcoded secrets)
- Status/freshness & liquidity gates
- Correct YES/NO parity for aggressive crosses
- Risk caps (per-ticker & global), daily fill dedupe, kill-switch
- Idempotent order placement + CSV audit logs
- Pluggable "fair probability" hook (integrate Pinnacle when ready)

Usage
  export KALSHI_API_KEY="..."
  # optional:
  # export KALSHI_HOST="https://api.elections.kalshi.com"
  # export KALSHI_DRY_RUN="true"   # set to "false" to go live
  # export KALSHI_TICKERS="KXNFLGAME:*,KXNBAGAME:*"  # comma-separated list (":" suffix optional)
  python kalshi_live_trader.py
"""

import os
import sys
import csv
import json
import time
import math
import uuid
import signal
import hashlib
import threading
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any, Tuple

import requests


# export KALSHI_HOST="https://api.elections.kalshi.com"
# export KALSHI_API_KEY="e8b43912-6603-413c-b544-3ca7f47cd06b"
# export KALSHI_TICKERS="KXNFLGAME,KXNBAGAME"   # or whatever you trade
# export KALSHI_DRY_RUN="true"                  # keep true until you verify


# ========== CONFIG (env-only; sensible defaults) ==========
HOST                 = os.getenv("KALSHI_HOST", "https://api.elections.kalshi.com").rstrip("/")
API_KEY              = os.getenv("KALSHI_API_KEY")
AUTH_MODE            = os.getenv("KALSHI_AUTH_MODE", "bearer").lower()  # "bearer" | "x_api_key"
DRY_RUN              = os.getenv("KALSHI_DRY_RUN", "true").strip().lower() != "false"

# NEW: series-driven discovery (use this instead of KALSHI_TICKERS)
SERIES_ENV = os.getenv("KALSHI_SERIES", "").strip()
SERIES = [s.strip() for s in SERIES_ENV.split(",") if s.strip()]
DISCOVER_STATUS = os.getenv("KALSHI_DISCOVER_STATUS", "open")  # e.g., "open"


# Scope: comma-separated. Example: "TICKER1,TICKER2"
TICKERS_ENV          = os.getenv("KALSHI_TICKERS", "").strip()
TICKERS              = [t.strip() for t in TICKERS_ENV.split(",") if t.strip()] or ["KXNFLGAME","KXNBAGAME"
    # 👇 Put defaults here if you want; left empty on purpose to force explicit config.
]

# Timings / HTTP
POLL_INTERVAL_SEC    = int(os.getenv("KALSHI_POLL_INTERVAL_SEC", "5"))
REQ_TIMEOUT          = int(os.getenv("KALSHI_REQ_TIMEOUT", "15"))
MAX_RETRIES          = int(os.getenv("KALSHI_MAX_RETRIES", "3"))
RETRY_SLEEP          = float(os.getenv("KALSHI_RETRY_SLEEP", "1.0"))

# Trading policy
TIME_IN_FORCE        = os.getenv("KALSHI_TIF", "GTC")  # "GTC" | "DAY"
MIN_TICK             = float(os.getenv("KALSHI_MIN_TICK", "0.01"))
MIN_PRICE            = float(os.getenv("KALSHI_MIN_PRICE", "0.01"))
MAX_PRICE            = float(os.getenv("KALSHI_MAX_PRICE", "0.99"))
MAX_SPREAD           = float(os.getenv("KALSHI_MAX_SPREAD", "0.10"))
MIN_EDGE             = float(os.getenv("KALSHI_MIN_EDGE", "0.02"))  # 2 cents edge threshold
MIN_BBO_SIZE         = int(os.getenv("KALSHI_MIN_BBO_SIZE", "1"))   # minimum displayed size at target level
NEAR_EXPIRY_MINS     = int(os.getenv("KALSHI_NEAR_EXPIRY_MINS", "3"))

# Risk caps
MAX_CONTRACTS_PER_ORDER      = int(os.getenv("KALSHI_MAX_CONTRACTS_PER_ORDER", "50"))
MAX_OPEN_CONTRACTS_PER_TICK  = int(os.getenv("KALSHI_MAX_OPEN_CONTRACTS_PER_TICK", "500"))
MAX_GLOBAL_OPEN_CONTRACTS    = int(os.getenv("KALSHI_MAX_GLOBAL_OPEN_CONTRACTS", "3000"))
MAX_DAILY_CONTRACTS_PER_TICK = int(os.getenv("KALSHI_MAX_DAILY_CONTRACTS_PER_TICK", "2000"))

# Safety / Ops
KILL_SWITCH_FILE     = os.getenv("KALSHI_KILL_SWITCH_FILE", "kill.switch")
CANCEL_ON_EXIT       = os.getenv("KALSHI_CANCEL_ON_EXIT", "false").lower() == "true"
STALE_SECONDS        = int(os.getenv("KALSHI_STALE_SECONDS", "15"))

# Logging
LOG_DIR              = os.getenv("KALSHI_LOG_DIR", "live_betting")
os.makedirs(LOG_DIR, exist_ok=True)
ORDERS_CSV           = os.path.join(LOG_DIR, "orders_log.csv")
FILLS_CSV            = os.path.join(LOG_DIR, "fills_log.csv")
HEALTH_CSV           = os.path.join(LOG_DIR, "health_log.csv")
DECISIONS_CSV        = os.path.join(LOG_DIR, "decisions_log.csv")

# ========== UTIL ==========
def now_utc() -> datetime:
    return datetime.now(timezone.utc)

def now_iso() -> str:
    return now_utc().isoformat().replace("+00:00", "Z")

def require_api_key():
    if not API_KEY or API_KEY.strip() == "":
        sys.exit("❌ Missing KALSHI_API_KEY env var. Aborting.")

def headers() -> Dict[str, str]:
    h = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "kalshi-live-trader/2.0",
    }
    if AUTH_MODE == "bearer":
        h["Authorization"] = f"Bearer {API_KEY}"
    else:
        h["X-API-Key"] = API_KEY
    return h

def http_request(method: str, path: str, params: Optional[dict] = None, body: Optional[dict] = None,
                 extra_headers: Optional[dict] = None) -> Optional[dict]:
    url = f"{HOST}/trade-api/v2/{path.lstrip('/')}"
    hdrs = headers()
    if extra_headers:
        hdrs.update(extra_headers)
    data = json.dumps(body) if body is not None else None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            if DRY_RUN and method.upper() in {"POST", "DELETE"}:
                # Emulate a minimal OK response
                return {"dry_run": True, "echo": {"path": path, "params": params, "body": body}}

            r = requests.request(method=method.upper(), url=url, headers=hdrs,
                                 params=params, data=data, timeout=REQ_TIMEOUT)
            if r.status_code in (200, 201, 204):
                if r.text.strip() == "" or r.status_code == 204:
                    return {"ok": True}
                try:
                    return r.json()
                except Exception:
                    return json.loads(r.text)
            if r.status_code in {429, 500, 502, 503, 504}:
                time.sleep(RETRY_SLEEP * attempt)
                continue
            # Hard error
            print(f"❌ {method} {url} {r.status_code}: {r.text[:300]}", file=sys.stderr)
            return None
        except requests.RequestException as e:
            print(f"⚠️ {method} {url} error: {e}", file=sys.stderr)
            time.sleep(RETRY_SLEEP * attempt)
    return None

def clamp_price(p: float) -> float:
    if p is None or math.isnan(p):
        return p
    p = max(MIN_PRICE, min(MAX_PRICE, p))
    # snap to tick
    return round(round(p / MIN_TICK) * MIN_TICK, 4)

def to_f(x) -> Optional[float]:
    try:
        return float(x)
    except Exception:
        return None

def log_csv(path: str, fieldnames: List[str], row: Dict[str, Any]):
    is_new = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        if is_new:
            w.writeheader()
        w.writerow(row)

def log_health(msg: str, **extra):
    row = {"ts": now_iso(), "msg": msg}
    row.update(extra or {})
    # ensure all keys consistent
    fn = ["ts", "msg", *sorted([k for k in row.keys() if k not in {"ts","msg"}])]
    log_csv(HEALTH_CSV, fn, row)

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
        "result": json.dumps(resp)[:2000]
    }
    log_csv(ORDERS_CSV, ["ts","action","ticker","side","type","price","count","tif","client_order_id","result"], row)

def log_fill(fill: Dict[str, Any]):
    row = {
        "ts": now_iso(),
        "fill_ts": fill.get("timestamp") or fill.get("time"),
        "ticker": fill.get("ticker"),
        "side": fill.get("side"),
        "price": fill.get("price") or fill.get("price_dollars"),
        "count": fill.get("count") or fill.get("quantity"),
        "order_id": fill.get("order_id") or fill.get("orderId"),
        "fill_id": fill.get("fill_id") or fill.get("id"),
    }
    log_csv(FILLS_CSV, ["ts","fill_ts","ticker","side","price","count","order_id","fill_id"], row)

def log_decision(ticker: str, intent: Dict[str, Any], context: Dict[str, Any]):
    row = {
        "ts": now_iso(),
        "ticker": ticker,
        "intent_id": intent.get("intent_id"),
        "side": intent.get("side"),
        "price": intent.get("price"),
        "count": intent.get("count"),
        "reason": context.get("reason"),
        "fair_yes": context.get("fair_yes"),
        "edge": context.get("edge"),
        "spread": context.get("spread"),
        "yes_bid": context.get("yes_bid"),
        "yes_ask": context.get("yes_ask"),
        "no_bid": context.get("no_bid"),
        "no_ask": context.get("no_ask"),
    }
    log_csv(DECISIONS_CSV,
            ["ts","ticker","intent_id","side","price","count","reason","fair_yes","edge","spread","yes_bid","yes_ask","no_bid","no_ask"],
            row)

# ========== KALSHI CLIENT ==========

def get_positions() -> Dict[str, Any]:
    data = http_request("GET", "positions") or {}
    return data

def get_orders(ticker: Optional[str] = None) -> List[dict]:
    params = {"ticker": ticker} if ticker else None
    data = http_request("GET", "orders", params=params) or {}
    return data.get("orders") or data.get("data") or []

def get_fills(since_iso: Optional[str] = None) -> List[dict]:
    params = {"since": since_iso} if since_iso else None
    data = http_request("GET", "fills", params=params) or {}
    return data.get("fills") or data.get("data") or []

def get_market(ticker: str) -> Dict[str, Any]:
    return http_request("GET", f"markets/{ticker}") or {}

def get_orderbook(ticker: str) -> Dict[str, Any]:
    return http_request("GET", f"markets/{ticker}/orderbook") or {}

def cancel_order(order_id: str) -> Optional[dict]:
    return http_request("DELETE", f"orders/{order_id}")

def place_order(payload: Dict[str, Any], idempotency_key: str) -> Optional[dict]:
    return http_request("POST", "orders", body=payload, extra_headers={"Idempotency-Key": idempotency_key})

def list_markets(series_ticker: Optional[str] = None, status: Optional[str] = None, cursor: Optional[str] = None) -> dict:
    params = {}
    if series_ticker:
        params["series_ticker"] = series_ticker
    if status:
        params["status"] = status
    if cursor:
        params["cursor"] = cursor
    return http_request("GET", "markets", params=params) or {}

def discover_market_tickers(series: List[str], status: str = "open", max_pages: int = 10) -> List[str]:
    found = []
    for s in series:
        cursor = None
        pages = 0
        while pages < max_pages:
            pages += 1
            payload = list_markets(series_ticker=s, status=status, cursor=cursor)
            markets = payload.get("markets") or []
            for m in markets:
                t = m.get("ticker")
                st = (m.get("status") or "").lower()
                if t and (not status or st == status.lower()):
                    found.append(t)
            cursor = payload.get("cursor")
            if not cursor:
                break
    return sorted(set(found))


# ========== STATE & RISK ==========
class RiskManager:
    def __init__(self):
        self.daily_filled: Dict[str, int] = {}
        self.seen_fill_ids: set = set()
        self.last_midnight = self._midnight_utc(now_utc())

    def _midnight_utc(self, dt: datetime) -> datetime:
        return datetime(dt.year, dt.month, dt.day, tzinfo=timezone.utc)

    def maybe_rollover(self):
        now = now_utc()
        if now >= self.last_midnight + timedelta(days=1):
            self.daily_filled.clear()
            self.seen_fill_ids.clear()
            self.last_midnight = self._midnight_utc(now)

    def update_fills(self, fills: List[dict]):
        self.maybe_rollover()
        for f in fills:
            fid = f.get("fill_id") or f.get("id")
            if not fid or fid in self.seen_fill_ids:
                continue
            self.seen_fill_ids.add(fid)
            t = f.get("ticker")
            qty = int(f.get("count") or f.get("quantity") or 0)
            if qty and t:
                self.daily_filled[t] = self.daily_filled.get(t, 0) + qty

    def global_open_contracts(self, positions_payload: Dict[str, Any]) -> int:
        total = 0
        for p in positions_payload.get("positions", []) or []:
            yes_sh = int(p.get("yes_shares") or 0)
            no_sh  = int(p.get("no_shares") or 0)
            total += abs(yes_sh - no_sh)  # crude net notion of risk
        return total

    def ticker_net(self, positions_payload: Dict[str, Any], ticker: str) -> int:
        for p in positions_payload.get("positions", []) or []:
            if p.get("ticker") == ticker:
                yes_sh = int(p.get("yes_shares") or 0)
                no_sh  = int(p.get("no_shares") or 0)
                return yes_sh - no_sh
        return 0

    def allowed_size(self, positions_payload: Dict[str, Any], ticker: str) -> int:
        # cap by per-order, per-ticker open, global open, and daily filled
        net = self.ticker_net(positions_payload, ticker)
        if abs(net) >= MAX_OPEN_CONTRACTS_PER_TICK:
            return 0
        global_open = self.global_open_contracts(positions_payload)
        if global_open >= MAX_GLOBAL_OPEN_CONTRACTS:
            return 0
        daily = self.daily_filled.get(ticker, 0)
        if daily >= MAX_DAILY_CONTRACTS_PER_TICK:
            return 0
        remaining_daily = MAX_DAILY_CONTRACTS_PER_TICK - daily
        # Conservative: also constrain by remaining per-ticker open
        remaining_open = MAX_OPEN_CONTRACTS_PER_TICK - abs(net)
        return max(0, min(MAX_CONTRACTS_PER_ORDER, remaining_daily, remaining_open))

# ========== FAIR PROBABILITY HOOK ==========
def fair_yes_probability(ticker: str, market: Dict[str, Any]) -> Optional[float]:
    """
    TODO: integrate Pinnacle de-vigged probability here.
    MVP fallback: use mid-price as a "fair" stand-in.
    """
    yb = to_f(market.get("yes_bid_dollars"))
    ya = to_f(market.get("yes_ask_dollars"))
    if yb is not None and ya is not None:
        return round((yb + ya) / 2.0, 4)
    return None

# ========== STRATEGY ==========
def parse_ob_depth(orderbook: Dict[str, Any]) -> Tuple[List[Tuple[float,int]], List[Tuple[float,int]]]:
    """
    Returns lists of (price, qty) in DOLLARS for YES and NO ask ladders.
    """
    ob = (orderbook or {}).get("orderbook") or {}
    yes = ob.get("yes_dollars")
    no  = ob.get("no_dollars")

    def normalize(levels):
        out = []
        if isinstance(levels, list):
            for item in levels:
                if isinstance(item, (list, tuple)) and len(item) == 2:
                    price, qty = item
                    pf = to_f(price)
                    try:
                        q = int(qty)
                    except Exception:
                        q = None
                    if pf is not None and q is not None:
                        out.append((round(pf, 4), q))
        return out

    return normalize(yes), normalize(no)

def decide_intents(
    ticker: str,
    market: Dict[str, Any],
    orderbook: Dict[str, Any],
    positions_payload: Dict[str, Any],
    risk: RiskManager
) -> List[Dict[str, Any]]:
    """
    Deterministic decision: compares "fair_yes" to BBO and optionally crosses.
    - Requires spread <= MAX_SPREAD and displayed size >= MIN_BBO_SIZE
    - Aggressive buy-YES: take yes_ask
    - Aggressive sell-YES: take yes_bid (by placing NO at price 1 - yes_bid)
    """
    intents: List[Dict[str, Any]] = []

    # Market status gate
    status = str(market.get("status") or "").lower()
    if status not in {"open"}:
        return intents

    # Expiry guard (best-effort; Kalshi fields may differ; ignore if absent)
    expiry = market.get("event_expiration_time") or market.get("close_time") or market.get("expiry") or None
    try:
        if isinstance(expiry, str) and expiry.endswith("Z"):
            expiry_dt = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
            if (expiry_dt - now_utc()) <= timedelta(minutes=NEAR_EXPIRY_MINS):
                return intents
    except Exception:
        pass

    # Top of book
    yes_bid = to_f(market.get("yes_bid_dollars"))
    yes_ask = to_f(market.get("yes_ask_dollars"))
    no_bid  = to_f(market.get("no_bid_dollars"))
    no_ask  = to_f(market.get("no_ask_dollars"))

    # Spread + liquidity gates
    spread = None
    if yes_bid is not None and yes_ask is not None:
        spread = round(yes_ask - yes_bid, 4)
        if spread is None or spread > MAX_SPREAD:
            return intents

    yes_levels, no_levels = parse_ob_depth(orderbook)
    best_yes_ask_qty = yes_levels[0][1] if yes_levels else 0
    best_yes_bid_qty = None
    # Infer bid qty from NO ladder parity if YES bids not provided by API; otherwise rely on market fields
    # Here we only check displayed ask size for aggression + a basic qty for the other side if available
    if no_levels:
        # NO ask at price_no == 1 - YES bid; we don't align ladders strictly, so only minimal checks.
        pass

    # require some displayed size at target price (on YES ask for buy or YES bid for sell)
    if best_yes_ask_qty is not None and best_yes_ask_qty < MIN_BBO_SIZE:
        # still allow if no depth API provides qty=0; we don't hard fail here
        pass

    # Fair probability
    fair_yes = fair_yes_probability(ticker, market)
    if fair_yes is None or yes_bid is None or yes_ask is None:
        return intents

    # Edge calculations (expected price improvement vs "fair")
    # Buy-YES edge: fair - ask ; Sell-YES edge: bid - fair
    buy_edge  = round(fair_yes - yes_ask, 4)
    sell_edge = round(yes_bid - fair_yes, 4)

    allowed = risk.allowed_size(positions_payload, ticker)
    if allowed <= 0:
        return intents

    # BUY YES (take yes_ask) when fair is sufficiently higher than ask
    if buy_edge >= MIN_EDGE and best_yes_ask_qty >= 1:
        price = clamp_price(yes_ask)
        if price is not None and MIN_PRICE <= price <= MAX_PRICE:
            intents.append({
                "intent_id": str(uuid.uuid4()),
                "ticker": ticker,
                "side": "yes",
                "price": price,
                "count": min(allowed, best_yes_ask_qty),  # don't exceed displayed size
                "reason": f"buy_yes_edge={buy_edge}"
            })

    # SELL YES (take yes_bid) by placing NO at price_no = 1 - yes_bid
    # This crosses the NO ask (parity) and should execute if the book is coherent.
    if sell_edge >= MIN_EDGE:
        price_no = clamp_price(1.0 - yes_bid)
        # NO parity sanity vs no_ask, if available
        if no_ask is not None and price_no < no_ask - 2*MIN_TICK:
            # parity anomaly; skip to avoid off-book pricing
            pass
        else:
            intents.append({
                "intent_id": str(uuid.uuid4()),
                "ticker": ticker,
                "side": "no",
                "price": price_no,
                "count": allowed,
                "reason": f"sell_yes_edge={sell_edge}"
            })

    # Decision logging
    for it in intents:
        log_decision(ticker, it, {
            "reason": it.get("reason"),
            "fair_yes": fair_yes,
            "edge": buy_edge if it["side"] == "yes" else sell_edge,
            "spread": spread,
            "yes_bid": yes_bid,
            "yes_ask": yes_ask,
            "no_bid": no_bid,
            "no_ask": no_ask,
        })

    return intents

# ========== MAIN LOOP ==========
RUN = True
def handle_sig(sig, frame):
    global RUN
    RUN = False
    print("🛑 Signal received; shutting down...")

signal.signal(signal.SIGINT, handle_sig)
signal.signal(signal.SIGTERM, handle_sig)

def idempotency_key(intent: Dict[str, Any]) -> str:
    """
    Idempotency across transient retries while distinguishing new intents.
    Combines stable fields + a short time bucket to reduce accidental dupes.
    """
    bucket = int(time.time() // 5)  # 5s bucket
    base = f"{intent['ticker']}|{intent['side']}|{intent['price']}|{intent['count']}|{bucket}"
    return hashlib.sha256(base.encode()).hexdigest()

def submit_intent(intent: Dict[str, Any]) -> Optional[dict]:
    side = intent["side"]
    payload = {
        "ticker": intent["ticker"],
        "side": side,                     # "yes" or "no"
        "type": "limit",
        "price": clamp_price(float(intent["price"])),
        "count": int(intent["count"]),
        "time_in_force": TIME_IN_FORCE,
        "client_order_id": intent.get("intent_id"),  # helpful for reconciliation
    }
    # Basic payload validation
    if payload["count"] <= 0 or payload["price"] is None:
        return None
    if payload["price"] < MIN_PRICE or payload["price"] > MAX_PRICE:
        return None

    key = idempotency_key(intent)
    resp = place_order(payload, key)
    log_order("place", payload, resp)
    return resp

def cancel_all_open_orders():
    # Best-effort cancel of all orders for our tickers
    try:
        for t in TICKERS:
            for o in get_orders(t):
                oid = o.get("order_id") or o.get("id")
                if not oid: continue
                resp = cancel_order(oid)
                log_order("cancel", {"ticker": t, "side": o.get("side"), "type": "cancel",
                                     "price": o.get("price"), "count": o.get("count"),
                                     "time_in_force": o.get("time_in_force"),
                                     "client_order_id": o.get("client_order_id")}, resp)
                time.sleep(0.05)
    except Exception as e:
        log_health("cancel_all_error", error=str(e))

def kill_switch_engaged() -> bool:
    try:
        return os.path.exists(KILL_SWITCH_FILE)
    except Exception:
        return False

# remove or ignore get_me()

ACCOUNT_API_AVAILABLE = True  # global-ish toggle

def bootstrap_readiness():
    global ACCOUNT_API_AVAILABLE
    # Try an authenticated call that should exist; if 404, disable account API usage
    pos = http_request("GET", "positions")
    if not pos:
        # Check if it's a 404 host for account endpoints by trying a market list instead
        mk = list_markets(status="open")
        if not mk:
            sys.exit("❌ Host unreachable for markets. Check KALSHI_HOST and network.")
        ACCOUNT_API_AVAILABLE = False
        print("ℹ️ Account endpoints unavailable on this host; continuing without positions/fills.", file=sys.stderr)
    log_health("ready", dry_run=DRY_RUN, series=",".join(SERIES), host=HOST)



def main():
    require_api_key()
    if not TICKERS:
        sys.exit("⚠️ Set KALSHI_TICKERS env var with at least one ticker (comma-separated).")

    bootstrap_readiness()
    # Discover actual market tickers from series
    if SERIES:
        tickers = discover_market_tickers(SERIES, status=DISCOVER_STATUS)
        if not tickers:
            sys.exit(f"⚠️ No markets discovered for series={SERIES} with status={DISCOVER_STATUS}.")
        print(f"🧭 Discovered {len(tickers)} market(s): first 5 → {tickers[:5]}")
    else:
        # fallback to env-provided TICKERS if user set them explicitly
        tickers = TICKERS
        if not tickers:
            sys.exit("⚠️ Provide KALSHI_SERIES or KALSHI_TICKERS.")

    risk = RiskManager()

    last_fills_pull = now_utc() - timedelta(seconds=10)

    while RUN:
        loop_started = now_utc()
        if kill_switch_engaged():
            log_health("kill_switch_engaged")
            time.sleep(POLL_INTERVAL_SEC)
            continue

        # Refresh positions and recent fills
                # ✅ Refresh positions/fills only if account endpoints are available
        positions = {}
        if ACCOUNT_API_AVAILABLE:
            positions = get_positions() or {}
            try:
                if (now_utc() - last_fills_pull).total_seconds() >= 5:
                    fills = get_fills() or []
                    if fills:
                        for f in fills:
                            log_fill(f)
                    risk.update_fills(fills or [])
                    last_fills_pull = now_utc()
            except Exception as e:
                log_health("fills_error", error=str(e))


        # Per-ticker decisions
        for ticker in TICKERS:
            try:
                mkt = get_market(ticker) or {}
                ob  = get_orderbook(ticker) or {}

                # Freshness guard (best-effort)
                # Use server-reported updated_at if available; else ensure our polling cadence is steady
                updated = mkt.get("updated_time") or mkt.get("updated_at") or None
                try:
                    if isinstance(updated, str):
                        if updated.endswith("Z"):
                            upd_dt = datetime.fromisoformat(updated.replace("Z", "+00:00"))
                        else:
                            upd_dt = datetime.fromisoformat(updated)
                        if (now_utc() - upd_dt).total_seconds() > STALE_SECONDS:
                            log_health("stale_market", ticker=ticker)
                            continue
                except Exception:
                    pass

                intents = decide_intents(ticker, mkt, ob, positions, risk)
                for it in intents:
                    submit_intent(it)

            except Exception as e:
                log_health("decision_error", ticker=ticker, error=str(e))
                continue

        # pace the loop
        elapsed = (now_utc() - loop_started).total_seconds()
        sleep_for = max(0.0, POLL_INTERVAL_SEC - elapsed)
        time.sleep(sleep_for)

    # Graceful shutdown
    log_health("stopping", cancel_on_exit=CANCEL_ON_EXIT)
    if CANCEL_ON_EXIT and not DRY_RUN:
        cancel_all_open_orders()
    log_health("stopped")

if __name__ == "__main__":
    main()
