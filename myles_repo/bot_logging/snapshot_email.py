from email.message import EmailMessage
import smtplib
from typing import Dict, Any, List, Optional
from config import settings
from app import state
from core.time import now_utc
from kalshi.markets import get_kalshi_markets, market_yes_mid
from odds_feed.betsapi import _fetch_odds_feed_live_events
from odds_feed.overlaps import get_overlapping_matches
from utils.names import normalize_tokens, expand_nba_abbreviations
from odds_feed.formatting import _format_score
from positions.metrics import _current_unrealized_and_equity
from kalshi.balance import get_kalshi_balance


def _positions_snapshot_text(live_games: Optional[List[Dict[str, Any]]] = None) -> str:
    active_positions = [p for p in state.positions if not p.get("settled", False)]

    lines = []

    lines.append("=" * 80)
    lines.append("LIVE TRADES")
    lines.append("=" * 80)

    if not active_positions:
        lines.append("No open positions.")
    else:
        lines.append("Match | Side | Qty | Entry | Live | P&L | ROI%")
        markets_cache: Dict[str, list] = {}
        total_unreal = 0.0
        rows_added = 0

        for pos in active_positions:
            qty = int(pos.get("stake", 0))
            if qty <= 0:
                continue

            evt = pos.get("event_ticker") or ""
            key = evt.lower()
            if key not in markets_cache:
                mkts = []
                if evt:
                    try:
                        mkts = get_kalshi_markets(evt, force_live=True) or []
                    except Exception as exc:
                        print(f"⚠️ Email snapshot: market fetch failed for {evt}: {exc}")
                markets_cache[key] = mkts

            mkts = markets_cache[key]
            market = next((m for m in mkts if m.get("ticker") == pos.get("market_ticker")), None)
            live_price = market_yes_mid(market) if market else None

            entry = float(pos.get("entry_price", 0.0))
            unrealized = None if live_price is None else (live_price - entry) * qty
            roi = None
            if unrealized is not None and entry > 0 and qty > 0:
                roi = unrealized / (qty * entry)

            live_str = "--" if live_price is None else f"{live_price:.2%}"
            unrealized_str = "--" if unrealized is None else f"${unrealized:,.2f}"
            roi_str = "--" if roi is None else f"{roi * 100:.2f}%"

            lines.append(
                f"{pos.get('match','?')} | {pos.get('side','').upper()} | {qty} | "
                f"{entry:.2%} | {live_str} | {unrealized_str} | {roi_str}"
            )

            if unrealized is not None:
                total_unreal += unrealized
            rows_added += 1

        if rows_added == 0:
            lines.append("No open positions.")

        total_cost_of_positions = 0.0
        for pos in active_positions:
            qty = int(pos.get("stake", 0))
            entry = float(pos.get("entry_price", 0.0))
            if qty > 0 and entry > 0:
                total_cost_of_positions += qty * entry

        session_equity = None
        current_cash = None
        if settings.PLACE_LIVE_KALSHI_ORDERS == "YES":
            try:
                current_cash = get_kalshi_balance()
            except Exception as exc:
                print(f"⚠️ Email snapshot: balance fetch failed: {exc}")
                current_cash = state.SESSION_START_BAL or 0.0
            session_equity = (current_cash or 0.0) + total_cost_of_positions + total_unreal
        else:
            current_cash = state.capital_sim + state.realized_pnl
            session_equity = state.capital_sim + state.realized_pnl + total_cost_of_positions + total_unreal

        session_roi_str = "--"
        if state.SESSION_START_BAL not in (None, 0):
            session_roi = (session_equity - state.SESSION_START_BAL) / state.SESSION_START_BAL
            session_roi_str = f"{session_roi * 100:.2f}%"

        lines.append("")
        lines.append("-" * 80)
        lines.append("CAPITAL BREAKDOWN:")
        lines.append(f"  Current Cash Balance: ${current_cash:,.2f}")
        lines.append(f"  Cost of Positions: ${total_cost_of_positions:,.2f}")
        lines.append(f"  Unrealized P&L: ${total_unreal:,.2f}")
        lines.append("-" * 80)
        lines.append(f"  Total Equity: ${session_equity:,.2f}")
        lines.append(f"  ROI since start: {session_roi_str}")

    lines.append("")
    lines.append("")
    lines.append("=" * 80)
    lines.append("LIVE GAMES BEING MONITORED")
    lines.append("=" * 80)

    if not live_games:
        lines.append("No live games currently being monitored.")
    else:
        lines.append("Game | Score | Time | Home Price | Away Price")
        lines.append("-" * 80)

        for match in live_games:
            home_original = match.get("home", "?")
            away_original = match.get("away", "?")

            ticker = match.get("ticker", "")
            is_nba_game = ticker.startswith("KXNBAGAME-") if ticker else False

            home_display = expand_nba_abbreviations(home_original) if is_nba_game else home_original
            away_display = expand_nba_abbreviations(away_original) if is_nba_game else away_original

            game_name = f"{away_display} vs {home_display}"

            score_snapshot = match.get("odds_feed", {}).get("score_snapshot")
            event_data = match
            score_str = _format_score(event_data, score_snapshot)

            period_clock = match.get("odds_feed", {}).get("period_clock")
            time_str = period_clock if period_clock else "--"

            home_price_str = "--"
            away_price_str = "--"

            if ticker:
                try:
                    markets = get_kalshi_markets(ticker, force_live=True) or []
                    if markets:
                        home_for_matching = expand_nba_abbreviations(home_original) if is_nba_game else home_original
                        away_for_matching = expand_nba_abbreviations(away_original) if is_nba_game else away_original

                        home_tokens = normalize_tokens(home_for_matching)
                        away_tokens = normalize_tokens(away_for_matching)

                        home_market = None
                        away_market = None

                        for m in markets:
                            yes_sub_title = (m.get("yes_sub_title") or "")
                            if is_nba_game:
                                yes_sub_title = expand_nba_abbreviations(yes_sub_title)

                            m_tokens = normalize_tokens(yes_sub_title)

                            if home_tokens & m_tokens:
                                home_market = m
                            elif away_tokens & m_tokens:
                                away_market = m

                        if home_market:
                            home_mid = market_yes_mid(home_market)
                            home_price_str = f"{home_mid:.2%}" if home_mid is not None else "--"

                        if away_market:
                            away_mid = market_yes_mid(away_market)
                            away_price_str = f"{away_mid:.2%}" if away_mid is not None else "--"
                except Exception as exc:
                    print(f"⚠️ Email snapshot: failed to fetch prices for {ticker}: {exc}")

            game_type_label = "[NBA]" if is_nba_game else "[NCAA]"
            lines.append(f"{game_type_label} {game_name} | {score_str} | {time_str} | {home_price_str} | {away_price_str}")

    timestamp = now_utc().isoformat()
    return f"Snapshot at {timestamp}\n" + "\n".join(lines)


def send_positions_email(reason: str = "hourly", live_games: Optional[List[Dict[str, Any]]] = None):
    if not settings.SEND_EMAIL_TURN_ON:
        return
    if not settings.EMAIL_SENDER or not settings.EMAIL_APP_PASSWORD:
        print("⚠️ Email disabled — missing EMAIL_SENDER or EMAIL_APP_PASSWORD.")
        return

    if not live_games:
        try:
            latest_raw_events = _fetch_odds_feed_live_events()
            live_games = get_overlapping_matches(preloaded_events=latest_raw_events)
            if live_games:
                print(f"📧 Email: Fetched {len(live_games)} live games for email report")
        except Exception as exc:
            print(f"⚠️ Email: Failed to fetch live games: {exc}")
            live_games = []

    body = _positions_snapshot_text(live_games=live_games)
    subject = f"Kalshi positions update ({reason})"

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.EMAIL_SENDER
    msg["To"] = settings.EMAIL_RECIPIENT
    msg.set_content(body)

    try:
        with smtplib.SMTP_SSL(settings.EMAIL_SMTP_HOST, settings.EMAIL_SMTP_PORT, timeout=15) as smtp:
            smtp.login(settings.EMAIL_SENDER, settings.EMAIL_APP_PASSWORD)
            smtp.send_message(msg)
        print(f"📧 Sent positions email to {settings.EMAIL_RECIPIENT} ({reason}).")
        print(f"Email sent to {settings.EMAIL_RECIPIENT}")
    except Exception as exc:
        print(f"⚠️ Failed to send positions email: {exc}")
