from datetime import timedelta
from app import state
from config import settings
from core.time import now_utc, parse_iso_utc
from kalshi.markets import get_kalshi_markets
from positions.io import save_positions
from utils.tickers import event_key


def refresh_position_tracking(active_matches):
    if settings.PRESERVE_MANUAL_POSITIONS:
        if settings.VERBOSE:
            print("🛡️ Manual positions enabled — refresh_position_tracking() skipped.")
        return

    tracked_event_keys = {
        event_key(m.get("ticker"))
        for m in active_matches
        if m.get("ticker")
    }

    tracked_market_tickers = set()
    for match in active_matches:
        for market in match.get("kalshi") or []:
            ticker = market.get("ticker")
            if ticker:
                tracked_market_tickers.add(ticker.upper())

    changed = False

    for p in state.positions:
        market_tkr = (p.get("market_ticker") or "").upper()
        event_tkr_key = event_key(p.get("event_ticker"))

        market_active = market_tkr and market_tkr in tracked_market_tickers
        event_active = event_tkr_key and event_tkr_key in tracked_event_keys

        if market_active or event_active:
            continue

        if not p.get("settled", False):
            print(f"⚠️ Market for {p['market_ticker']} no longer active — marking as settled/untracked")
            p["settled"] = True
            p["tracking_status"] = "lost"
            changed = True

    if changed:
        save_positions()


def purge_stale_positions(hours: int = 4, active_matches: list = None):
    active_matches = active_matches or []

    cutoff = now_utc() - timedelta(hours=hours)
    before = len(state.positions)
    kept = []

    for p in state.positions:
        try:
            entry_ts = parse_iso_utc(p.get("entry_time")) if p.get("entry_time") else None
            last_seen = parse_iso_utc(p.get("last_seen_live")) if p.get("last_seen_live") else None

            if p.get("settled"):
                kept.append(p)
                continue

            mkts = get_kalshi_markets(p.get("event_ticker", ""), force_live=True) or []
            live_m = next((m for m in mkts
                           if m.get("ticker") == p.get("market_ticker")
                           and m.get("status") == "active"
                           and (m.get("yes_bid") or m.get("yes_ask"))), None)

            if live_m:
                kept.append(p)
                continue

            ref_ts = last_seen or entry_ts
            if ref_ts and ref_ts < cutoff:
                print(
                    f"🧹 Purging stale (>{hours}h unseen on Kalshi): "
                    f"{p.get('match')} {p.get('side')} @ {p.get('entry_price'):.2%}"
                )
                continue

            kept.append(p)

        except Exception as e:
            print(f"⚠️ purge_stale_positions error on {p.get('market_ticker')}: {e}")
            kept.append(p)

    if len(kept) != before:
        print(f"🧹 Removed {before - len(kept)} stale positions (> {hours}h).")
    state.positions = kept
    save_positions()


def purge_old_positions(hours: int = 24):
    cutoff = now_utc() - timedelta(hours=hours)
    before = len(state.positions)
    state.positions = [
        p for p in state.positions
        if not p.get("settled") or parse_iso_utc(p["entry_time"]) > cutoff
    ]
    if len(state.positions) != before:
        print(f"🧹 Purged {before - len(state.positions)} old settled positions.")
        save_positions()


def purge_stale_live_positions(hours: int = 12):
    cutoff = now_utc() - timedelta(hours=hours)
    kept = []
    removed = 0
    for pos in state.positions:
        if pos.get("settled"):
            kept.append(pos)
            continue
        entry_ts = pos.get("entry_time")
        if not entry_ts:
            kept.append(pos)
            continue
        try:
            entry_dt = parse_iso_utc(entry_ts)
        except Exception:
            kept.append(pos)
            continue
        if entry_dt < cutoff:
            removed += 1
            print(
                f"🧹 Removing stale live position (> {hours}h): "
                f"{pos.get('match')} {pos.get('side')} @ {pos.get('entry_price'):.2%}"
            )
            continue
        kept.append(pos)
    if removed:
        state.positions = kept
        save_positions()


def check_time_based_exits():
    if not settings.TIME_BASED_EXITS_ENABLED:
        return

    current_time = now_utc()
    threshold_seconds = settings.TIME_EXIT_THRESHOLD_MINUTES * 60.0

    for p in state.positions:
        if p.get("settled", False):
            continue

        entry_time_str = p.get("entry_time")
        if not entry_time_str:
            continue

        try:
            entry_time = parse_iso_utc(entry_time_str)
            hold_duration = (current_time - entry_time).total_seconds()

            if hold_duration >= threshold_seconds:
                p["time_exit_triggered"] = True
                if settings.VERBOSE:
                    print(
                        f"⏰ Time-based exit triggered for {p.get('market_ticker')} "
                        f"(held {hold_duration/60:.1f} minutes, threshold {settings.TIME_EXIT_THRESHOLD_MINUTES:.1f} minutes)"
                    )
        except Exception as e:
            if settings.VERBOSE:
                print(f"⚠️ Error checking time-based exit for {p.get('market_ticker')}: {e}")
