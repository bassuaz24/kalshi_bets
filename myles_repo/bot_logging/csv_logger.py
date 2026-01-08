import csv
import os
import time
from typing import Dict, Any
from config import settings
from app import state
from core.time import now_utc
from kalshi.fees import kalshi_fee_per_contract
from kalshi.markets import format_price
from positions.metrics import _current_unrealized_and_equity, _roi_pct_from_equity
from utils.tickers import event_key
from positions.queries import event_is_neutralized
from math_calculations.ev import ev_exit_yes

LOG_FILE = "bot_log_basketball.csv"
LOG_FIELDS = [
    "ts", "event", "match", "ticker",
    "side", "market_ticker",
    "yes_bid", "yes_ask",
    "odds_feed_home_prob", "odds_feed_away_prob",
    "entry_price", "exit_price",
    "exit_fee",
    "total_fees",
    "qty", "pnl", "entry_fee",
    "realized_pnl", "unrealized_pnl", "equity",
    "roi_pct", "note",
]


def _bump_fill(kind: str):
    if kind == "placed":
        state.METRICS["orders_placed"] += 1
    elif kind == "filled":
        state.METRICS["orders_filled"] += 1
    elif kind == "timeout_cancel":
        state.METRICS["orders_timeout_cancel"] += 1


def _metrics_flush_periodic():
    if not settings.WRITE_SESSION_METRICS:
        return

    path = "session_metrics_basketball.csv"
    placed = state.METRICS["orders_placed"] or 1
    fill_rate = state.METRICS["orders_filled"] / placed
    avg_slip = (
        state.METRICS["avg_slippage_bps_sum"] / state.METRICS["avg_slippage_bps_n"]
        if state.METRICS["avg_slippage_bps_n"]
        else 0.0
    )

    row = {
        "ts": now_utc().isoformat(),
        "orders_placed": state.METRICS["orders_placed"],
        "orders_filled": state.METRICS["orders_filled"],
        "orders_timeout_cancel": state.METRICS["orders_timeout_cancel"],
        "fill_rate": round(fill_rate, 4),
        "avg_slippage_bps": round(avg_slip, 2),
        "missed_hedge_band": state.METRICS["missed_hedge_band"],
        "missed_hedge_cap": state.METRICS["missed_hedge_cap"],
        "missed_hedge_kelly": state.METRICS["missed_hedge_kelly"],
        **{f"skip_{k}": v for k, v in list(state.METRICS["skip_counts"].items())[:5]},
    }
    _append_csv(path, row, fixed_fields=list(row.keys()))


def _append_csv(path, row, fixed_fields=None, add_timestamp=False):
    if not any([
        settings.WRITE_SNAPSHOTS, settings.WRITE_EVALS, settings.WRITE_BOT_LOG, settings.WRITE_TRADES_CSV,
        settings.WRITE_SESSION_METRICS, settings.WRITE_TRADE_METRICS, settings.WRITE_BACKTEST_FEED,
    ]):
        return

    if add_timestamp:
        row = {"ts": now_utc().isoformat(), **row}

    cols = list(fixed_fields or [])
    for k in row.keys():
        if k not in cols:
            cols.append(k)

    write_header = not os.path.exists(path) or os.path.getsize(path) == 0

    with open(path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=cols)
        if write_header:
            writer.writeheader()
        writer.writerow({k: row.get(k, "") for k in cols})


def log_trade(trade):
    if not settings.WRITE_TRADES_CSV:
        return

    if not trade.get("stake") or trade["stake"] <= 0:
        return

    try:
        from kalshi.positions import get_live_positions
        live_positions = get_live_positions()
        live_keys = {(p["ticker"], p["side"]) for p in live_positions}
        local_key = (trade.get("market_ticker"), trade.get("side"))
        if local_key not in live_keys:
            print(f"⚠️ Not yet visible on Kalshi ({local_key}) — logging anyway.")
    except Exception as e:
        print(f"⚠️ Live confirm error ({e}) — logging anyway.")

    path = "trades_basketball.csv"
    write_header = not os.path.exists(path) or os.path.getsize(path) == 0
    with open(path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=trade.keys())
        if write_header:
            writer.writeheader()
        writer.writerow(trade)

    side_display = trade.get("side_name", trade.get("side", "UNKNOWN"))
    print(f"📝 Trade logged: {trade.get('match')} {side_display} x{trade.get('stake')}")


def log_backtest_metrics(row: dict):
    if not settings.WRITE_TRADE_METRICS:
        return

    path = "trade_metrics_basketball.csv"
    cols = [
        "ts", "match", "market_ticker", "side", "entry_price", "exit_price",
        "odds_prob", "spread", "fair_ev", "kelly_fraction", "volatility_mode",
        "stake", "pnl_cash", "pnl_pct", "hold_seconds",
    ]
    row = {"ts": now_utc().isoformat(), **row}
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        if f.tell() == 0:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in cols})


def log_snapshot_scan(match: dict):
    if not settings.WRITE_SNAPSHOTS:
        return

    from kalshi.markets import market_yes_mid

    def _spread(yb_raw, ya_raw):
        if yb_raw is None or ya_raw is None:
            return None
        return max(0.0, (ya_raw - yb_raw) / 100.0)

    def _mid(yb, ya):
        if yb is None and ya is None:
            return None
        return (yb + ya) / 2.0 if (yb is not None and ya is not None) else (yb or ya)

    def _prices(m):
        if not m:
            return None, None, None, None, None

        try:
            yb_raw = m.get("yes_bid")
            ya_raw = m.get("yes_ask")
            yb = format_price(yb_raw)
            ya = format_price(ya_raw)
            if ya is not None:
                nb = 1.0 - ya
            elif yb is not None:
                nb = 1.0 - yb
            else:
                nb = None
            return yb_raw, ya_raw, yb, ya, nb
        except Exception as e:
            print(f"⚠️ _prices() error: {e}")
            return None, None, None, None, None

    def _find_mkt_for(label: str, kalshi_markets: list):
        from utils.names import normalize_name
        lab = normalize_name(label)
        for m in kalshi_markets or []:
            st = normalize_name(m.get("yes_sub_title", ""))
            if lab in st or st in lab:
                return m
        return None

    def _fee_at(px):
        return kalshi_fee_per_contract(px) if px is not None else None

    match_id = match.get("match") or match.get("ticker") or "unknown"
    now_ts = time.time()
    last_ts = state._last_snapshot_write_per_match.get(match_id, 0.0)
    if (now_ts - last_ts) < float(settings.SNAPSHOT_MIN_INTERVAL_SECS):
        return
    state._last_snapshot_write_per_match[match_id] = now_ts
    state._snapshot_scan_counter += 1
    if state._snapshot_scan_counter % max(1, settings.SNAPSHOT_EVERY_N_SCANS) != 0:
        return

    home = match["home"]
    away = match["away"]
    kalshi = match.get("kalshi") or []
    home_mkt = _find_mkt_for(home, kalshi)
    away_mkt = _find_mkt_for(away, kalshi)

    h_yb_raw, h_ya_raw, h_yb, h_ya, h_nb = _prices(home_mkt)
    a_yb_raw, a_ya_raw, a_yb, a_ya, a_nb = _prices(away_mkt)

    home_spread = _spread(h_yb_raw, h_ya_raw)
    away_spread = _spread(a_yb_raw, a_ya_raw)
    home_mid = _mid(h_yb, h_ya)
    away_mid = _mid(a_yb, a_ya)

    cons_h = fair_h = cons_a = fair_a = None
    odds_snapshot = match.get("odds_feed") or {}
    hp = odds_snapshot.get("home_prob")
    ap = odds_snapshot.get("away_prob")
    if hp is not None and home_mid is not None and h_yb is not None and h_ya is not None:
        cons_h, fair_h = ev_exit_yes(hp, home_mid, h_yb, h_ya)
    if ap is not None and away_mid is not None and a_yb is not None and a_ya is not None:
        cons_a, fair_a = ev_exit_yes(ap, away_mid, a_yb, a_ya)

    evt = match.get("ticker", "")
    evt_key = event_key(evt)
    open_yes_home = sum(
        p["stake"]
        for p in state.positions
        if event_key(p.get("event_ticker")) == evt_key
        and p.get("market_ticker") == ((home_mkt or {}).get("ticker"))
        and p.get("side") == "yes"
    )
    open_yes_away = sum(
        p["stake"]
        for p in state.positions
        if event_key(p.get("event_ticker")) == evt_key
        and p.get("market_ticker") == ((away_mkt or {}).get("ticker"))
        and p.get("side") == "yes"
    )
    exposure_evt_usd = sum(
        p["stake"] * p["entry_price"]
        for p in state.positions
        if event_key(p.get("event_ticker")) == evt_key
    )
    neutralized_flag = event_is_neutralized(evt)

    period_clock_raw = odds_snapshot.get("period_clock", "")
    game_period = ""
    time_remaining = ""
    if period_clock_raw and " - " in period_clock_raw:
        parts = period_clock_raw.strip().split(" - ")
        if len(parts) == 2:
            game_period = parts[0].strip()
            time_remaining = parts[1].strip()

    row = {
        "ts": now_utc().isoformat(),
        "date_code": match.get("date", ""),
        "match": match["match"],
        "home": home,
        "away": away,
        "score_snapshot": odds_snapshot.get("score_snapshot", ""),
        "game_period": game_period,
        "time_remaining": time_remaining,
        "home_odds": odds_snapshot.get("home_odds", ""),
        "away_odds": odds_snapshot.get("away_odds", ""),
        "home_prob": hp,
        "away_prob": ap,
        "ticker_found": True,
        "tickers_tried": "",
        "ticker": evt,
        "kalshi_home_yes_bid": h_yb,
        "kalshi_home_yes_ask": h_ya,
        "kalshi_home_no_bid": h_nb,
        "kalshi_away_yes_bid": a_yb,
        "kalshi_away_yes_ask": a_ya,
        "kalshi_away_no_bid": a_nb,
        "home_spread": home_spread,
        "away_spread": away_spread,
        "home_mid": home_mid,
        "away_mid": away_mid,
        "home_yes_bid_size": (home_mkt or {}).get("yes_bid_size", ""),
        "home_yes_ask_size": (home_mkt or {}).get("yes_ask_size", ""),
        "away_yes_bid_size": (away_mkt or {}).get("yes_bid_size", ""),
        "away_yes_ask_size": (away_mkt or {}).get("yes_ask_size", ""),
        "home_fee_at_bid": _fee_at(h_yb),
        "home_fee_at_ask": _fee_at(h_ya),
        "away_fee_at_bid": _fee_at(a_yb),
        "away_fee_at_ask": _fee_at(a_ya),
        "edge_home_abs": (hp - home_mid) if (hp is not None and home_mid is not None) else "",
        "edge_away_abs": (ap - away_mid) if (ap is not None and away_mid is not None) else "",
        "cons_ev_home": cons_h,
        "fair_ev_home": fair_h,
        "cons_ev_away": cons_a,
        "fair_ev_away": fair_a,
        "event_ticker": evt,
        "home_market_ticker": (home_mkt or {}).get("ticker", ""),
        "away_market_ticker": (away_mkt or {}).get("ticker", ""),
        "open_yes_home_qty": open_yes_home,
        "open_yes_away_qty": open_yes_away,
        "exposure_event_usd": exposure_evt_usd,
        "neutralized_flag": neutralized_flag,
        "log_score": match.get("log_score", ""),
        "odds_ts": now_utc().isoformat(),
        "kalshi_fetch_ts": now_utc().isoformat(),
        "scan_seq": state._snapshot_scan_counter,
    }

    fixed = [
        "ts", "date_code", "match", "home", "away",
        "score_snapshot", "game_period", "time_remaining",
        "home_odds", "away_odds", "home_prob", "away_prob",
        "ticker_found", "tickers_tried", "ticker",
        "kalshi_home_yes_bid", "kalshi_home_yes_ask", "kalshi_home_no_bid",
        "kalshi_away_yes_bid", "kalshi_away_yes_ask", "kalshi_away_no_bid",
        "home_spread", "away_spread", "home_mid", "away_mid",
        "home_yes_bid_size", "home_yes_ask_size", "away_yes_bid_size", "away_yes_ask_size",
        "home_fee_at_bid", "home_fee_at_ask", "away_fee_at_bid", "away_fee_at_ask",
        "edge_home_abs", "edge_away_abs", "cons_ev_home", "fair_ev_home", "cons_ev_away", "fair_ev_away",
        "event_ticker", "home_market_ticker", "away_market_ticker",
        "open_yes_home_qty", "open_yes_away_qty", "exposure_event_usd", "neutralized_flag",
        "odds_ts", "kalshi_fetch_ts", "scan_seq",
        "log_score",
    ]

    _append_csv(os.path.join(settings.BASE_DIR, "market_snapshots_for_duke_basketball.csv"), row, fixed_fields=fixed)


def log_eval(row: dict):
    if not settings.WRITE_EVALS:
        return
    if settings.WRITE_EVALS_TRADE_ONLY and row.get("decision") not in ("yes", "no"):
        return
    row = {"ts": now_utc().isoformat(), **row}
    fixed = [
        "ts", "event_ticker", "market_ticker", "match", "side_label",
        "odds_prob", "yes_bid", "yes_ask", "kalshi_price",
        "edge", "kelly_fraction", "spread", "cost_buffer", "logit_gap",
        "decision",
    ]
    _append_csv("market_evals_basketball.csv", row, fixed_fields=fixed)


def log_backtest_feed(row: dict):
    if not settings.WRITE_BACKTEST_FEED:
        return
    fixed = [
        "ts", "match", "event_ticker", "market_ticker", "side_label",
        "books_used", "books_weights", "books_sampled",
        "home_prob", "away_prob", "odds_prob",
        "yes_bid", "yes_ask", "kalshi_mid", "kalshi_price", "spread",
        "edge_pct", "fair_ev", "cons_ev", "rt_ev", "kelly_fraction", "volatility_mode",
        "capital", "min_qty_required", "planned_qty", "has_event_position", "is_hedge", "decision",
        "cost_buffer",
        "score_snapshot", "game_period", "time_remaining",
    ]
    _append_csv("backtest_feed_basketball.csv", row, fixed_fields=fixed, add_timestamp=True)


def _ensure_log_header():
    if not os.path.exists(LOG_FILE) or os.path.getsize(LOG_FILE) == 0:
        with open(LOG_FILE, "w", newline="") as f:
            csv.DictWriter(f, fieldnames=LOG_FIELDS).writeheader()


def _write_log_row(row: dict):
    if not settings.WRITE_BOT_LOG:
        return
    _ensure_log_header()
    with open(LOG_FILE, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LOG_FIELDS)
        for k in LOG_FIELDS:
            row.setdefault(k, "")
        w.writerow(row)


def _format_price_f(x):
    try:
        return round(float(x), 4)
    except Exception:
        return ""


def log_entry_row(position: dict, ticker: str):
    unreal, equity = _current_unrealized_and_equity()
    roi = _roi_pct_from_equity(equity)
    _write_log_row({
        "ts": now_utc().isoformat(),
        "event": "entry",
        "match": position.get("match", ""),
        "ticker": ticker,
        "side": position.get("side", ""),
        "market_ticker": position.get("market_ticker", ""),
        "yes_bid": "",
        "yes_ask": "",
        "odds_feed_home_prob": "",
        "odds_feed_away_prob": "",
        "entry_price": _format_price_f(position.get("entry_price")),
        "exit_price": "",
        "qty": int(position.get("stake", 0)),
        "pnl": "",
        "realized_pnl": round(state.realized_pnl, 4),
        "unrealized_pnl": round(unreal, 4),
        "equity": round(equity, 4),
        "roi_pct": round(roi, 4),
        "note": "",
    })


def log_exit_row(position: dict, exit_price: float, pnl_cash: float, settled: bool = False):
    unreal, equity = _current_unrealized_and_equity()
    roi = _roi_pct_from_equity(equity)
    exit_fee = 0.0 if settled else kalshi_fee_per_contract(exit_price, is_maker=True)
    entry_fee = kalshi_fee_per_contract(position.get("entry_price"), is_maker=False)
    total_fees = entry_fee + (0.0 if settled else exit_fee)
    _write_log_row({
        "ts": now_utc().isoformat(),
        "event": "exit",
        "match": position.get("match", ""),
        "ticker": position.get("event_ticker", ""),
        "side": position.get("side", ""),
        "market_ticker": position.get("market_ticker", ""),
        "yes_bid": "",
        "yes_ask": "",
        "odds_feed_home_prob": "",
        "odds_feed_away_prob": "",
        "entry_price": _format_price_f(position.get("entry_price")),
        "exit_price": _format_price_f(exit_price),
        "qty": int(position.get("stake", 0)),
        "pnl": round(float(pnl_cash), 4),
        "realized_pnl": round(state.realized_pnl, 4),
        "unrealized_pnl": round(unreal, 4),
        "equity": round(equity, 4),
        "roi_pct": round(roi, 4),
        "note": "",
        "exit_fee": round(exit_fee, 4),
        "total_fees": round(total_fees, 4),
    })
