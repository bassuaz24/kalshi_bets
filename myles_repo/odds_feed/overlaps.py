import time
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List, Iterable
from dateutil import parser
from config import settings
from app import state
from core.time import UTC
from odds_feed.betsapi import fetch_event_moneyline, _fetch_odds_feed_live_events
from odds_feed.filters import _is_ncaa_event, _is_nba_event
from odds_feed.formatting import _format_status, _format_score, _normalize_start_ts
from odds_feed.odds_cache import _load_odds_snapshot, _save_odds_snapshot
from kalshi.markets import get_kalshi_markets, format_price
from math_calculations.ev import devig_proportional, devig_shin_two_way
from utils.tickers import make_ncaa_event_ticker, make_nba_event_ticker
from utils.names import normalize_tokens


def _implied_prob(value: Optional[float]) -> Optional[float]:
    if not isinstance(value, (int, float)) or value <= 0:
        return None
    return 1.0 / float(value)


def get_overlapping_matches(preloaded_events: Optional[List[Dict[str, Any]]] = None):
    overlaps = []

    def _kalshi_side_prices(markets, home_name, away_name):
        home_tokens = normalize_tokens(home_name)
        away_tokens = normalize_tokens(away_name)
        home_mkt, away_mkt = None, None

        for m in markets or []:
            ysub_raw = (m.get("yes_sub_title") or "")
            ytokens = normalize_tokens(ysub_raw)
            if home_tokens & ytokens:
                home_mkt = m
                print(f"✅ Matched HOME '{home_name}' ↔ '{ysub_raw}' ({ytokens})")
            elif away_tokens & ytokens:
                away_mkt = m
                print(f"✅ Matched AWAY '{away_name}' ↔ '{ysub_raw}' ({ytokens})")

        if not home_mkt or not away_mkt:
            print(f"⚠️ Kalshi side match failed for {home_name} vs {away_name}")
            print(f"   home_tokens={list(home_tokens)}, away_tokens={list(away_tokens)}")
            print(f"   Kalshi titles normalized: {[list(normalize_tokens(m.get('yes_sub_title'))) for m in (markets or [])]}")

        def _extract_prices(m):
            if not m:
                return None, None, None
            uh = (m or {}).get("response_price_units", "usd_cent")
            yb = format_price(m.get("yes_bid"), units_hint=uh)
            ya = format_price(m.get("yes_ask"), units_hint=uh)
            nb = (1.0 - ya) if ya is not None else ((1.0 - yb) if yb is not None else None)
            return yb, ya, nb

        h_yb, h_ya, h_nb = _extract_prices(home_mkt)
        a_yb, a_ya, a_nb = _extract_prices(away_mkt)
        return h_yb, h_ya, h_nb, a_yb, a_ya, a_nb

    if preloaded_events is not None:
        events = preloaded_events
    else:
        events = _fetch_odds_feed_live_events()
    _load_odds_snapshot()

    if not events:
        print("⚠️ No live basketball matches found.")
        return overlaps

    print("\n🏀 Live Basketball Matches – With Moneyline Odds (NCAA + NBA)\n")

    print(f"\n   Filtering for NCAA and NBA events...", end="", flush=True)
    ncaa_events = [e for e in events if _is_ncaa_event(e)]
    nba_events = [e for e in events if _is_nba_event(e)]

    all_basketball_events = ncaa_events + nba_events
    trading_status = "monitoring only" if not settings.ENABLE_NBA_TRADING else "monitoring & trading"
    print(f" ✓ ({len(ncaa_events)} NCAA, {len(nba_events)} NBA [{trading_status}], {len(all_basketball_events)} total)")

    if not all_basketball_events:
        return overlaps

    print(f"\n   🧠 Matching basketball events to Kalshi tickers:", flush=True)
    ticker_matches = {}
    for e in all_basketball_events:
        home_obj = e.get("home")
        away_obj = e.get("away")
        if isinstance(home_obj, dict):
            home = home_obj.get("name") or home_obj.get("short_name") or home_obj.get("display_name")
        else:
            home = home_obj or (e.get("team_home") or {}).get("name") if isinstance(e.get("team_home"), dict) else None
        if isinstance(away_obj, dict):
            away = away_obj.get("name") or away_obj.get("short_name") or away_obj.get("display_name")
        else:
            away = away_obj or (e.get("team_away") or {}).get("name") if isinstance(e.get("team_away"), dict) else None
        if not home or not away:
            print(f"      ⚠️ Skipping event (missing team names): home={home}, away={away}", flush=True)
            continue

        start_time = e.get("time") or e.get("starts") or e.get("start_at") or e.get("starts_at")
        try:
            if isinstance(start_time, (int, float)):
                dt_utc = datetime.fromtimestamp(start_time, tz=UTC)
            elif start_time:
                try:
                    dt_utc = parser.isoparse(str(start_time))
                except (ValueError, TypeError):
                    dt_utc = datetime.now(UTC)
            else:
                dt_utc = datetime.now(UTC)
        except Exception:
            dt_utc = datetime.now(UTC)

        match_date = dt_utc
        date_prior = match_date - timedelta(days=1)

        is_nba = _is_nba_event(e)
        event_type = "NBA" if is_nba else "NCAA"
        ticker_candidates = []
        for tag, h, a, ts in [
            ("today", home, away, match_date),
            ("today", away, home, match_date),
            ("yesterday", home, away, date_prior),
            ("yesterday", away, home, date_prior),
        ]:
            if is_nba:
                for ticker_option in make_nba_event_ticker(h, a, ts):
                    ticker_candidates.append((ticker_option, tag))
            else:
                for ticker_option in make_ncaa_event_ticker(h, a, ts):
                    ticker_candidates.append((ticker_option, tag))
        print(f"      [{event_type}] {away} vs {home}:", flush=True)
        ticker = None
        if not ticker_candidates:
            print(f"         ⚠️ No ticker candidates generated", flush=True)
        else:
            print(f"         🔍 Trying {len(ticker_candidates)} ticker candidate(s):", flush=True)
            for ticker_try, tag in ticker_candidates:
                print(f"            - {ticker_try} ({tag})", flush=True)
        for ticker_try, tag in ticker_candidates:
            time.sleep(0.15)

            markets = get_kalshi_markets(ticker_try, force_live=True)

            if markets is None:
                print(f"         ⚠️ Rate limited, waiting 2 seconds before retry...", flush=True)
                time.sleep(2.0)
                markets = get_kalshi_markets(ticker_try, force_live=True)
                if markets is None:
                    print(f"         ⚠️ Still rate limited, skipping remaining tickers for this game", flush=True)
                    break

            if markets:
                print(f"         ✅ Found {len(markets)} markets for {ticker_try}", flush=True)
                ticker = ticker_try
                break

        if ticker:
            evt_id_key = e.get("id")
            ticker_matches[evt_id_key] = ticker
        else:
            print(f"         ⚠️ No Kalshi markets found for any ticker", flush=True)

    print(f"\n   Fetching odds and building board...", flush=True)
    seen_matchups = set()
    events_with_odds = 0
    for e in all_basketball_events:
        tournament = e.get("tournament") or {}
        league = (
            e.get("league_name")
            or (e.get("league") or {}).get("name")
            or tournament.get("name")
            or ""
        ).strip()
        home_obj = e.get("home")
        away_obj = e.get("away")
        if isinstance(home_obj, dict):
            home = home_obj.get("name") or home_obj.get("short_name") or home_obj.get("display_name")
        else:
            home = home_obj or (e.get("team_home") or {}).get("name") if isinstance(e.get("team_home"), dict) else None

        if isinstance(away_obj, dict):
            away = away_obj.get("name") or away_obj.get("short_name") or away_obj.get("display_name")
        else:
            away = away_obj or (e.get("team_away") or {}).get("name") if isinstance(e.get("team_away"), dict) else None

        if not home or not away:
            print(f"         ⚠️ Skipping event: missing team names (home={home}, away={away})")
            continue

        matchup_key = (home.strip().lower(), away.strip().lower())
        if matchup_key in seen_matchups:
            print(f"         ⚠️ Skipping duplicate: {away} vs {home}")
            continue
        seen_matchups.add(matchup_key)

        is_nba = _is_nba_event(e)
        event_type = "NBA" if is_nba else "NCAA"

        start_time = e.get("time") or e.get("starts") or e.get("start_at") or e.get("starts_at")
        if isinstance(start_time, (int, float)):
            dt_utc = datetime.fromtimestamp(start_time, tz=UTC)
        else:
            dt_utc = datetime.now(UTC)
        match_date = dt_utc
        date_code = dt_utc.strftime("%d%b%y").upper()

        evt_id = str(e.get("id"))
        if not evt_id:
            print(f"         ⚠️ Skipping {away} vs {home}: No event ID")
            continue

        try:
            moneyline = fetch_event_moneyline(evt_id)
            if not moneyline:
                print(f"         ⚠️ No odds available from BetsAPI for: {away} vs {home}")
                continue
        except RuntimeError as exc:
            print(f"         ⚠️ Error fetching odds for {away} vs {home}: {exc}")
            continue
        except Exception as exc:
            print(f"         ⚠️ Unexpected error for {away} vs {home}: {exc}")
            continue

        home_dec = float(moneyline["home_odds"])
        away_dec = float(moneyline["away_odds"])

        implied_home = 1.0 / home_dec
        implied_away = 1.0 / away_dec

        fair_prop_home, fair_prop_away = devig_proportional([implied_home, implied_away])
        fair_shin_home, fair_shin_away = devig_shin_two_way(home_dec, away_dec)

        if settings.USE_SHIN_DEVIG:
            home_prob = fair_shin_home
            away_prob = fair_shin_away
        else:
            home_prob = fair_prop_home
            away_prob = fair_prop_away

        home_odds = home_dec
        away_odds = away_dec

        odds_snapshot = {
            "home_prob": home_prob,
            "away_prob": away_prob,
            "home_odds": home_odds,
            "away_odds": away_odds,
            "score_snapshot": moneyline.get("score_snapshot"),
            "period_clock": moneyline.get("period_clock"),
            "last_update_ts": time.time(),
            "last_update_iso": datetime.utcnow().isoformat() + "Z",
        }

        time.sleep(settings.EVENT_ODDS_SLEEP)

        evt_id_for_lookup = e.get("id")
        ticker = ticker_matches.get(evt_id_for_lookup) if evt_id_for_lookup else None
        kalshi_markets = None

        if ticker:
            kalshi_markets = get_kalshi_markets(ticker, force_live=True)
            if kalshi_markets is None:
                print(f"      ⚠️ {away} vs {home}: Rate limited while fetching ticker {ticker}")
                time.sleep(2.0)
                kalshi_markets = get_kalshi_markets(ticker, force_live=True) or []
            elif not kalshi_markets:
                print(f"      ⚠️ {away} vs {home}: Ticker {ticker} found but no markets returned")
        else:
            match_date = dt_utc
            date_prior = match_date - timedelta(days=1)

            is_nba_fallback = _is_nba_event(e)
            ticker_candidates = []
            for tag, h, a, ts in [
                ("today", home, away, match_date),
                ("today", away, home, match_date),
                ("yesterday", home, away, date_prior),
                ("yesterday", away, home, date_prior),
            ]:
                if is_nba_fallback:
                    for ticker_option in make_nba_event_ticker(h, a, ts):
                        ticker_candidates.append((ticker_option, tag))
                else:
                    for ticker_option in make_ncaa_event_ticker(h, a, ts):
                        ticker_candidates.append((ticker_option, tag))

            for ticker_try, tag in ticker_candidates:
                time.sleep(0.15)

                markets = get_kalshi_markets(ticker_try, force_live=True)

                if markets is None:
                    print(f"         ⚠️ Rate limited, waiting 2 seconds before retry...", flush=True)
                    time.sleep(2.0)
                    markets = get_kalshi_markets(ticker_try, force_live=True)
                    if markets is None:
                        print(f"         ⚠️ Still rate limited, skipping remaining tickers for this game", flush=True)
                        break

                if markets:
                    ticker = ticker_try
                    kalshi_markets = markets
                    break

        if not home_odds or not away_odds or home_prob is None or away_prob is None:
            print(
                f"         ⚠️ Skipping {away} vs {home}: Invalid odds (home_odds={home_odds}, "
                f"away_odds={away_odds}, home_prob={home_prob}, away_prob={away_prob})"
            )
            continue

        events_with_odds += 1

        if kalshi_markets:
            kh_yb, kh_ya, kh_nb, ka_yb, ka_ya, ka_nb = _kalshi_side_prices(kalshi_markets, home, away)
        else:
            kh_yb, kh_ya, kh_nb, ka_yb, ka_ya, ka_nb = None, None, None, None, None, None

        odds_ts = odds_snapshot.get("last_update_ts") or time.time()
        odds_ts_iso = odds_snapshot.get("last_update_iso") or datetime.fromtimestamp(odds_ts).isoformat()

        if kalshi_markets and home_odds and away_odds and home_prob is not None and away_prob is not None:
            status_str = _format_status(e)
            score_str = _format_score(e, moneyline.get("score_snapshot"))
            period_clock = moneyline.get("period_clock")

            event_type = "NBA" if _is_nba_event(e) else "NCAA"
            print(
                f"📅 {match_date.strftime('%b %d')} | [{event_type}] {away} vs {home} | "
                f"League: {league.upper()} | Status: {status_str}",
                end="",
            )
            if score_str and score_str != "0-0":
                print(f" | Score: {score_str}", end="")
            print()

            if _is_nba_event(e) and not settings.ENABLE_NBA_TRADING:
                print(
                    "   🚫 NBA trading is DISABLED (ENABLE_NBA_TRADING = False) - monitoring only, "
                    "no trades will be placed"
                )

            if period_clock:
                print(f"   Clock: {period_clock}", end="")
            if moneyline.get("score_snapshot"):
                print(f" | Score snapshot: {moneyline.get('score_snapshot')}", end="")
            if period_clock or moneyline.get("score_snapshot"):
                print()

            print(f"  – {home}: {home_odds:.3f} ➔ {home_prob:.2%}")
            print(f"  – {away}: {away_odds:.3f} ➔ {away_prob:.2%}")
            print(f"  → Kalshi Ticker Matched: {ticker}")
            print("——————————————————————————————————————————————————")

            has_prices = any(x is not None for x in [kh_yb, kh_ya, kh_nb, ka_yb, ka_ya, ka_nb])
            if not has_prices:
                print(f"      ⚠️ {away} vs {home}: Has Kalshi markets but no bid/ask prices yet")
            print(f"      ✅ Adding to overlaps: {away} vs {home}")
            overlaps.append({
                "id": e.get("id"),
                "match": f"{home} vs {away}",
                "date": date_code,
                "ticker": ticker,
                "home": home,
                "away": away,
                "waiting_books": [],
                "books_used": [],
                "needs_odds_update": False,
                "odds_feed": {
                    "home_odds": float(home_odds) if home_odds is not None else None,
                    "away_odds": float(away_odds) if away_odds is not None else None,
                    "home_prob": home_prob,
                    "away_prob": away_prob,
                    "score_snapshot": odds_snapshot.get("score_snapshot"),
                    "period_clock": odds_snapshot.get("period_clock"),
                    "books_sampled": 1,
                    "last_update_ts": odds_ts,
                    "last_update_iso": odds_ts_iso,
                },
                "kalshi": kalshi_markets,
            })
        elif not kalshi_markets:
            pass

    print(f" ✓ ({events_with_odds} events with odds, {len(overlaps)} games with both odds and Kalshi markets)")
    return overlaps


def _odds_feed_homeaway_avgs(
    event_id: int,
    prev_snapshot: Optional[Dict[str, Dict[str, float]]],
    require_update: bool = True,
) -> tuple:
    _ = require_update
    _ = prev_snapshot

    from odds_feed.betsapi import _betsapi_request

    payload = _betsapi_request(settings.BETSAPI_EVENT_ODDS_PATH, {"event_id": event_id})
    odds = payload.get("results", {}).get("odds") or {}
    entries = odds.get(settings.BASKETBALL_MONEYLINE_KEY) or []

    event_snapshot: Dict[str, Dict[str, float]] = {}
    all_entries: List[Dict[str, Any]] = []
    book_count = 0
    book_names: List[str] = []

    def _book_probabilities(dec_home, dec_away) -> tuple:
        try:
            dh = float(dec_home)
            da = float(dec_away)
        except (TypeError, ValueError):
            return None, None
        if dh <= 0 or da <= 0:
            return None, None
        if settings.USE_SHIN_DEVIG:
            return devig_shin_two_way(dh, da)
        probs = devig_proportional([1.0 / dh, 1.0 / da])
        return probs[0], probs[1]

    def _fmt_book_odds(val: Optional[float]) -> str:
        return f"{float(val):.2f}" if isinstance(val, (int, float)) else "?"

    def _simple_mean(entries: List[Dict[str, Any]]) -> tuple:
        valid = [e for e in entries if e["home"] is not None and e["away"] is not None]
        if not valid:
            return None, None, []
        home_avg = sum(e["home"] for e in valid) / len(valid)
        away_avg = sum(e["away"] for e in valid) / len(valid)
        return home_avg, away_avg, [e["label"] for e in valid]

    for record in entries:
        home_od = record.get("home_od")
        away_od = record.get("away_od")

        if home_od in (None, "-", "") or away_od in (None, "-", ""):
            continue

        try:
            home_dec = float(home_od)
            away_dec = float(away_od)
        except (TypeError, ValueError):
            continue

        if home_dec <= 0 or away_dec <= 0:
            continue

        book_id = str(record.get("bookmaker_id") or record.get("id") or f"book_{book_count}")
        book_label = record.get("bookmaker_name") or "UNKNOWN"

        event_snapshot[book_id] = {
            "outcome_0": home_dec,
            "outcome_1": away_dec,
        }

        hp, ap = _book_probabilities(home_dec, away_dec)
        if hp is None or ap is None:
            continue

        book_count += 1
        home_odds_disp = _fmt_book_odds(home_dec)
        away_odds_disp = _fmt_book_odds(away_dec)
        book_names.append(f"{book_label} (H:{home_odds_disp} | A:{away_odds_disp})")

        entry = {
            "home": hp,
            "away": ap,
            "label": book_label,
            "book_id": book_id,
        }

        all_entries.append(entry)

        time.sleep(settings.EVENT_ODDS_SLEEP)

    if not all_entries:
        return None, None, book_count, 0, 0, event_snapshot, book_names, []

    home_avg, away_avg, used_labels = _simple_mean(all_entries)
    if home_avg is None or away_avg is None or not all_entries:
        return None, None, book_count, 0, 0, event_snapshot, book_names, []

    total = home_avg + away_avg
    if total > 0:
        home_avg /= total
        away_avg /= total

    avg_count = len(all_entries)
    changed_count = avg_count
    effective_count = avg_count

    return (
        home_avg,
        away_avg,
        book_count,
        effective_count,
        avg_count,
        event_snapshot,
        book_names,
        used_labels,
    )


def get_odds_feed_events(
    overlap_map: Optional[Dict[int, Dict[str, Any]]] = None,
    raw_events: Optional[List[Dict[str, Any]]] = None,
):
    overlap_ids = set(overlap_map.keys()) if overlap_map else None

    last_err = None
    current_snapshot: Dict[str, Dict[str, Dict[str, float]]] = {}
    _load_odds_snapshot()
    if overlap_map is not None and not overlap_map:
        print("⚠️ No Kalshi overlaps provided — skipping odds-feed scan.")
        return []

    try:
        if raw_events is None:
            raw_events = _fetch_odds_feed_live_events()
        adapted = []
        for evt in raw_events:
            home_obj = evt.get("home")
            away_obj = evt.get("away")
            if isinstance(home_obj, dict):
                home = home_obj.get("name") or home_obj.get("short_name") or home_obj.get("display_name")
            else:
                home = home_obj or (evt.get("team_home") or {}).get("name") if isinstance(evt.get("team_home"), dict) else None

            if isinstance(away_obj, dict):
                away = away_obj.get("name") or away_obj.get("short_name") or away_obj.get("display_name")
            else:
                away = away_obj or (evt.get("team_away") or {}).get("name") if isinstance(evt.get("team_away"), dict) else None

            if not home or not away:
                continue

            if not _is_ncaa_event(evt) and not _is_nba_event(evt):
                continue

            evt_id = evt.get("id")
            if evt_id is None:
                continue
            if overlap_ids is not None and evt_id not in overlap_ids:
                continue

            prev_books = state._odds_prev_snapshot.get(str(evt_id)) if state._odds_snapshot_loaded else None
            (
                home_prob,
                away_prob,
                books_sampled,
                changed_count,
                avg_count,
                evt_snapshot,
                book_names,
                books_used_now,
            ) = _odds_feed_homeaway_avgs(
                evt_id,
                prev_books,
            )
            current_snapshot[str(evt_id)] = evt_snapshot

            if home_prob is None or away_prob is None:
                print(f"         ⚠️ No probabilities available from BetsAPI for: {away} vs {home}")
                continue

            home_odds = float(1.0 / max(home_prob, 1e-6))
            away_odds = float(1.0 / max(away_prob, 1e-6))
            quote_ts = time.time()

            if overlap_map:
                match_info = overlap_map.get(evt_id)
                if match_info:
                    match_info.pop("_waiting_notice_stage", None)

            start_time = evt.get("time")
            if start_time:
                try:
                    if isinstance(start_time, (int, float)):
                        start_time = datetime.fromtimestamp(start_time, tz=UTC).isoformat()
                    else:
                        start_time = _normalize_start_ts(str(start_time))
                except Exception:
                    start_time = _normalize_start_ts(None)

            adapted.append({
                "id": evt_id,
                "home": home,
                "away": away,
                "league_name": "NBA" if _is_nba_event(evt) else "NCAA",
                "starts": start_time or _normalize_start_ts(None),
                "periods": {
                    "num_0": {
                        "money_line": {
                            "home": home_odds,
                            "away": away_odds,
                        }
                    }
                },
                "odds_feed": {
                    "home_prob": home_prob,
                    "away_prob": away_prob,
                    "books_sampled": avg_count,
                    "last_update_ts": quote_ts,
                    "last_update_iso": datetime.fromtimestamp(quote_ts).isoformat(),
                    "books_used": books_used_now,
                },
            })

        if current_snapshot:
            _save_odds_snapshot(current_snapshot)

        if adapted:
            return adapted

        if current_snapshot:
            last_err = "No NCAA/NBA games with updated sportsbook odds."
        else:
            last_err = "No live NCAA/NBA odds returned from BetsAPI."
    except Exception as exc:
        last_err = str(exc)

    print(f"❌ Odds-feed fetch failed: {last_err}")
    return []
