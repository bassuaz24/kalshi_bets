"""
OddsAPI client for fetching market data.
Data is separated by league for sports, by market otherwise.
"""

import os
import sys
import requests
import pandas as pd
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, timedelta, date
import pytz
from pathlib import Path

# Add base directory to path
_BASE_ROOT = Path(__file__).parent.parent.absolute()
if str(_BASE_ROOT) not in sys.path:
    sys.path.insert(0, str(_BASE_ROOT))

from config import settings

UTC = pytz.utc
CST = pytz.timezone("America/Chicago")


def _as_cst_datetime(value) -> datetime:
    """Convert value to CST datetime."""
    if value is None:
        raise ValueError("Missing datetime value")
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = UTC.localize(dt)
    return dt.astimezone(CST)


def convert_to_cst(value) -> str:
    """Convert datetime to CST string."""
    return _as_cst_datetime(value).strftime("%Y-%m-%d %H:%M:%S %Z")


def fetch_odds(sport_key: str) -> Optional[List[Dict[str, Any]]]:
    """Fetch odds from OddsAPI for a sport."""
    if not settings.ODDS_API_KEY:
        print(f"⚠️ ODDS_API_KEY not set, skipping fetch for {sport_key}")
        return None

    url = f"{settings.ODDS_API_BASE}/sports/{sport_key}/odds/"
    params = {
        "apiKey": settings.ODDS_API_KEY,
        "regions": settings.ODDS_API_REGION,
        "markets": settings.ODDS_API_MARKETS,
        "bookmakers": ",".join(settings.ODDS_API_BOOKMAKERS),
        "oddsFormat": "decimal",
        "dateFormat": "iso"
    }

    try:
        resp = requests.get(url, params=params, timeout=10)
        
        # #region agent log
        with open('/Users/Brett/kdata/kalshi_bets/.cursor/debug.log', 'a') as f:
            import json
            f.write(json.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"I","location":"oddsapi_client.py:61","message":"API response received","data":{"sport_key":sport_key,"status_code":resp.status_code,"url":url},"timestamp":int(datetime.now().timestamp()*1000)}) + '\n')
        # #endregion
        
        if resp.status_code != 200:
            # #region agent log
            with open('/Users/Brett/kdata/kalshi_bets/.cursor/debug.log', 'a') as f:
                f.write(json.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"I","location":"oddsapi_client.py:65","message":"API error","data":{"sport_key":sport_key,"status_code":resp.status_code,"error_text":resp.text[:200]},"timestamp":int(datetime.now().timestamp()*1000)}) + '\n')
            # #endregion
            print(f"❌ Error fetching {sport_key}: {resp.status_code} - {resp.text[:200]}")
            return None
        data = resp.json()
        
        # #region agent log
        with open('/Users/Brett/kdata/kalshi_bets/.cursor/debug.log', 'a') as f:
            import json
            sample_game = data[0] if data and len(data) > 0 else None
            sample_bookmakers = len(sample_game.get('bookmakers', [])) if sample_game else 0
            sample_bookmaker_names = [bm.get('title', '') for bm in sample_game.get('bookmakers', [])] if sample_game else []
            all_bookmaker_names = []
            for game in (data or []):
                for bm in game.get('bookmakers', []):
                    bm_name = bm.get('title', '')
                    if bm_name and bm_name not in all_bookmaker_names:
                        all_bookmaker_names.append(bm_name)
            f.write(json.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"I","location":"oddsapi_client.py:72","message":"API data parsed","data":{"sport_key":sport_key,"games_count":len(data) if data else 0,"sample_game_id":sample_game.get('id') if sample_game else None,"sample_home":sample_game.get('home_team') if sample_game else None,"sample_away":sample_game.get('away_team') if sample_game else None,"sample_bookmakers":sample_bookmakers,"sample_bookmaker_names":sample_bookmaker_names,"all_bookmaker_names":all_bookmaker_names,"configured_bookmakers":settings.ODDS_API_BOOKMAKERS},"timestamp":int(datetime.now().timestamp()*1000)}) + '\n')
        # #endregion
        
        # Debug: Log API response structure
        if settings.VERBOSE and data:
            print(f"📡 OddsAPI returned {len(data)} games for {sport_key}")
            if len(data) > 0:
                # Sample first game structure
                sample = data[0]
                print(f"   Sample game structure:")
                print(f"     - ID: {sample.get('id')}")
                print(f"     - Home: {sample.get('home_team')}")
                print(f"     - Away: {sample.get('away_team')}")
                print(f"     - Commence: {sample.get('commence_time')}")
                print(f"     - Bookmakers: {len(sample.get('bookmakers', []))}")
                if sample.get('bookmakers'):
                    for bm in sample.get('bookmakers', [])[:2]:  # First 2 bookmakers
                        print(f"       - {bm.get('title')}: {len(bm.get('markets', []))} markets")
        
        return data
    except Exception as e:
        print(f"❌ Exception fetching {sport_key}: {e}")
        return None


def fetch_kalshi_markets(event_ticker: Optional[str] = None) -> List[Dict[str, Any]]:
    """Fetch all active Kalshi markets, optionally filtered by event ticker."""
    from kalshi.markets import get_kalshi_markets
    
    if event_ticker:
        markets = get_kalshi_markets(event_ticker, force_live=True) or []
        return markets
    
    # If no event ticker, we'd need to fetch all events first
    # For now, return empty list - caller should provide event ticker
    return []


def normalize_odds_data(sport_name: str, games: List[Dict[str, Any]], target_dates: set) -> Tuple[Dict[str, List[Dict[str, Any]]], List[Dict[str, Any]]]:
    """Normalize OddsAPI data to rows organized by date and market.
    
    Returns:
        Tuple of (rows_by_date, skipped_games)
    """
    rows_by_date = {}
    skipped_games = []  # Track skipped games with details
    current_timestamp = datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S %Z")
    
    # Track filtering statistics
    stats = {
        "total_games": len(games),
        "missing_commence_time": 0,
        "datetime_conversion_error": 0,
        "date_filtered": 0,
        "no_bookmakers": 0,
        "games_processed": 0,
        "games_with_no_rows": 0,  # Games that passed filters but produced no data
        "bookmakers_with_no_markets": 0,
        "markets_with_no_outcomes": 0,
        "outcomes_missing_price": 0,
        "rows_created": 0
    }

    for game in games:
        game_time = game.get("commence_time")
        home_team = game.get("home_team", "Unknown")
        away_team = game.get("away_team", "Unknown")
        game_id = game.get("id", "")
        sport_title = game.get("sport_title", "")
        bookmakers_count = len(game.get("bookmakers", []))
        
        if not game_time:
            stats["missing_commence_time"] += 1
            skipped_games.append({
                "timestamp": current_timestamp,
                "sport": sport_name,
                "league": sport_title,
                "game_id": game_id,
                "home_team": home_team,
                "away_team": away_team,
                "commence_time": "",
                "skip_reason": "missing_commence_time",
                "bookmakers_count": bookmakers_count,
                "details": "Game missing commence_time field"
            })

            continue
            
        try:
            game_time_cst = _as_cst_datetime(game_time)
            commence_time_str = convert_to_cst(game_time_cst)
        except Exception as e:
            stats["datetime_conversion_error"] += 1
            skipped_games.append({
                "timestamp": current_timestamp,
                "sport": sport_name,
                "league": sport_title,
                "game_id": game_id,
                "home_team": home_team,
                "away_team": away_team,
                "commence_time": str(game_time),
                "skip_reason": "datetime_conversion_error",
                "bookmakers_count": bookmakers_count,
                "details": f"Error converting datetime: {str(e)}"
            })
            if settings.VERBOSE:
                print(f"⚠️ Skipping game {home_team} vs {away_team}: datetime conversion error: {e}")
            continue
            
        game_date = game_time_cst.date()
        if game_date not in target_dates:
            stats["date_filtered"] += 1
            skipped_games.append({
                "timestamp": current_timestamp,
                "sport": sport_name,
                "league": sport_title,
                "game_id": game_id,
                "home_team": home_team,
                "away_team": away_team,
                "commence_time": commence_time_str,
                "skip_reason": "date_filtered",
                "bookmakers_count": bookmakers_count,
                "details": f"Game date {game_date} not in target dates {target_dates}"
            })
            continue

        bookmakers = game.get("bookmakers", [])
        if not bookmakers:
            stats["no_bookmakers"] += 1
            skipped_games.append({
                "timestamp": current_timestamp,
                "sport": sport_name,
                "league": sport_title,
                "game_id": game_id,
                "home_team": home_team,
                "away_team": away_team,
                "commence_time": commence_time_str,
                "skip_reason": "no_bookmakers",
                "bookmakers_count": 0,
                "details": "Game has no bookmakers available"
            })
            if settings.VERBOSE:
                print(f"⚠️ Skipping game {home_team} vs {away_team}: no bookmakers available")
            continue
        
        stats["games_processed"] += 1
        rows_before = stats["rows_created"]
        
        # Count markets and outcomes for diagnostics
        total_markets = 0
        total_outcomes = 0
        bookmaker_names = []

        for bookmaker in bookmakers:
            book_name = bookmaker["title"]
            bookmaker_names.append(book_name)
            markets = bookmaker.get("markets", [])
            if not markets:
                stats["bookmakers_with_no_markets"] += 1
                if settings.VERBOSE:
                    print(f"⚠️ Bookmaker {book_name} for {home_team} vs {away_team} has no markets")
                continue
                
            for market in markets:
                market_type = market.get("key")
                outcomes = market.get("outcomes", [])
                total_markets += 1
                
                if not outcomes:
                    stats["markets_with_no_outcomes"] += 1
                    if settings.VERBOSE:
                        print(f"⚠️ Market {market_type} from {book_name} for {home_team} vs {away_team} has no outcomes")
                    continue

                for outcome in outcomes:
                    price = outcome.get("price")
                    if price is None:
                        stats["outcomes_missing_price"] += 1
                        continue
                    total_outcomes += 1

                    rows_by_date.setdefault(game_date, []).append({
                        "sport": sport_name,
                        "league": sport_title,
                        "game_id": game_id,
                        "start_time": commence_time_str,
                        "bookmaker": book_name,
                        "market": market_type,
                        "team": outcome.get("name"),
                        "price": outcome.get("price"),
                        "point": outcome.get("point", None),
                        "home_team": home_team,
                        "away_team": away_team
                    })
                    stats["rows_created"] += 1
        
        # Check if this game produced any rows
        if stats["rows_created"] == rows_before:
            stats["games_with_no_rows"] += 1
            skipped_games.append({
                "timestamp": current_timestamp,
                "sport": sport_name,
                "league": sport_title,
                "game_id": game_id,
                "home_team": home_team,
                "away_team": away_team,
                "commence_time": commence_time_str,
                "skip_reason": "games_with_no_rows",
                "bookmakers_count": len(bookmakers),
                "bookmakers": ", ".join(bookmaker_names),
                "total_markets": total_markets,
                "total_outcomes": total_outcomes,
                "details": f"Game passed filters but produced no rows. Bookmakers: {len(bookmakers)}, Markets: {total_markets}, Outcomes: {total_outcomes}"
            })
            if settings.VERBOSE:
                print(f"⚠️ Game {home_team} vs {away_team} (ID: {game_id}) passed filters but produced no rows")
                print(f"   Bookmakers: {len(bookmakers)}, Markets per bookmaker: {[len(b.get('markets', [])) for b in bookmakers]}")
    
    # Print summary statistics
    if stats["total_games"] > 0:
        print(f"📊 OddsAPI filtering stats for {sport_name}:")
        print(f"   Total games: {stats['total_games']}")
        print(f"   Processed: {stats['games_processed']}")
        print(f"   Rows created: {stats['rows_created']}")
        if stats["missing_commence_time"] > 0:
            print(f"   ⚠️ Missing commence_time: {stats['missing_commence_time']}")
        if stats["datetime_conversion_error"] > 0:
            print(f"   ⚠️ Datetime conversion errors: {stats['datetime_conversion_error']}")
        if stats["date_filtered"] > 0:
            print(f"   ⚠️ Date filtered out: {stats['date_filtered']} (target dates: {target_dates})")
        if stats["no_bookmakers"] > 0:
            print(f"   ⚠️ No bookmakers: {stats['no_bookmakers']}")
        if stats["games_with_no_rows"] > 0:
            print(f"   ⚠️ Games with no rows (CRITICAL): {stats['games_with_no_rows']} - These games passed filters but produced no data!")
        if stats["bookmakers_with_no_markets"] > 0:
            print(f"   ⚠️ Bookmakers with no markets: {stats['bookmakers_with_no_markets']}")
        if stats["markets_with_no_outcomes"] > 0:
            print(f"   ⚠️ Markets with no outcomes: {stats['markets_with_no_outcomes']}")
        if stats["outcomes_missing_price"] > 0:
            print(f"   ⚠️ Outcomes missing price: {stats['outcomes_missing_price']}")
    
    # #region agent log
    with open('/Users/Brett/kdata/kalshi_bets/.cursor/debug.log', 'a') as f:
        import json
        f.write(json.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"I","location":"oddsapi_client.py:325","message":"normalize_odds_data stats","data":{"sport_name":sport_name,"stats":stats,"rows_by_date_keys":[str(k) for k in rows_by_date.keys()],"rows_by_date_counts":{str(k):len(v) for k,v in rows_by_date.items()},"skipped_games_count":len(skipped_games)},"timestamp":int(datetime.now().timestamp()*1000)}) + '\n')
    # #endregion

    return rows_by_date, skipped_games


def save_skipped_games(skipped_games: List[Dict[str, Any]], filepath: Path):
    """Save skipped games to CSV file."""
    if not skipped_games:
        return
    
    os.makedirs(filepath.parent, exist_ok=True)
    df = pd.DataFrame(skipped_games)
    
    # Ensure consistent column order
    columns = [
        "timestamp", "sport", "league", "game_id", "home_team", "away_team",
        "commence_time", "skip_reason", "bookmakers_count", "bookmakers",
        "total_markets", "total_outcomes", "details"
    ]
    
    # Only include columns that exist in the data
    existing_columns = [col for col in columns if col in df.columns]
    # Add any additional columns that weren't in the list
    for col in df.columns:
        if col not in existing_columns:
            existing_columns.append(col)
    
    df = df[existing_columns]
    
    # Append to existing file if it exists
    if filepath.exists():
        try:
            df_existing = pd.read_csv(filepath)
            df_combined = pd.concat([df_existing, df], ignore_index=True)
            # Remove duplicates based on game_id and timestamp
            df_combined = df_combined.drop_duplicates(subset=["game_id", "timestamp"], keep="last")  # type: ignore
            df_combined.to_csv(filepath, index=False)
        except Exception as e:
            # If append fails, just overwrite
            if settings.VERBOSE:
                print(f"⚠️ Failed to append to {filepath}: {e}, overwriting instead")
            df.to_csv(filepath, index=False)
    else:
        df.to_csv(filepath, index=False)


def save_market_data(data: List[Dict[str, Any]], filepath: Path, market_type: Optional[str] = None, append: bool = True):
    """Save market data to CSV file.
    
    Args:
        data: List of dictionaries to save
        filepath: Path to CSV file
        market_type: Optional market type (for logging)
        append: If True and file exists, append data (default: True)
    """
    if not data:
        return

    os.makedirs(filepath.parent, exist_ok=True)
    df_new = pd.DataFrame(data)
    
    columns = [
        "sport", "league", "game_id", "start_time",
        "bookmaker", "market", "team", "price", "point",
        "home_team", "away_team"
    ]
    
    # Only include columns that exist in the data
    existing_columns = [col for col in columns if col in df_new.columns]
    df_new = df_new[existing_columns]
    
    # Append to existing file if it exists and append=True
    if append and filepath.exists():
        try:
            df_existing = pd.read_csv(filepath)
            # Combine and remove duplicates based on all columns
            df_combined = pd.concat([df_existing, df_new], ignore_index=True)
            df_combined = df_combined.drop_duplicates()
            df_combined.to_csv(filepath, index=False)
        except Exception as e:
            # If append fails, just overwrite
            if settings.VERBOSE:
                print(f"⚠️ Failed to append to {filepath}: {e}, overwriting instead")
            df_new.to_csv(filepath, index=False)
    else:
        df_new.to_csv(filepath, index=False)


def collect_data_running(output_dir: Optional[Path] = None, target_date: Optional[date] = None) -> Dict[str, Any]:
    """Collect market data when algorithm is running.
    
    Args:
        output_dir: Optional output directory (default: DATA_DIR / target_date)
        target_date: Optional target date (default: today)
    
    Returns:
        Dictionary with collected data organized by league/market.
    """
    if target_date is None:
        target_date = datetime.now(CST).date()
    
    if output_dir is None:
        output_dir = settings.DATA_DIR / target_date.strftime("%Y-%m-%d")
    output_dir.mkdir(parents=True, exist_ok=True)

    target_dates = {target_date}  # Only collect data for the specified date, not tomorrow

    collected_data = {}

    # Collect from OddsAPI (sports data by league)
    all_skipped_games = []  # Collect all skipped games across sports
    
    for sport_name, sport_key in settings.SPORT_KEYS.items():
        # #region agent log
        with open('/Users/Brett/kdata/kalshi_bets/.cursor/debug.log', 'a') as f:
            import json
            f.write(json.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"I","location":"oddsapi_client.py:430","message":"Processing sport","data":{"sport_name":sport_name,"sport_key":sport_key},"timestamp":int(datetime.now().timestamp()*1000)}) + '\n')
        # #endregion
        
        print(f"📡 Fetching odds for {sport_name} ({sport_key})...")
        data = fetch_odds(sport_key)
        
        # #region agent log
        with open('/Users/Brett/kdata/kalshi_bets/.cursor/debug.log', 'a') as f:
            import json
            f.write(json.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"I","location":"oddsapi_client.py:439","message":"After fetch_odds","data":{"sport_name":sport_name,"sport_key":sport_key,"data_is_none":data is None,"data_len":len(data) if data else 0},"timestamp":int(datetime.now().timestamp()*1000)}) + '\n')
        # #endregion
        
        if not data:
            # #region agent log
            with open('/Users/Brett/kdata/kalshi_bets/.cursor/debug.log', 'a') as f:
                import json
                f.write(json.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"I","location":"oddsapi_client.py:446","message":"No data returned, skipping","data":{"sport_name":sport_name,"sport_key":sport_key},"timestamp":int(datetime.now().timestamp()*1000)}) + '\n')
            # #endregion
            continue

        rows_by_date, skipped_games = normalize_odds_data(sport_name, data, target_dates)
        
        # #region agent log
        with open('/Users/Brett/kdata/kalshi_bets/.cursor/debug.log', 'a') as f:
            import json
            f.write(json.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"I","location":"oddsapi_client.py:453","message":"After normalize_odds_data","data":{"sport_name":sport_name,"sport_key":sport_key,"rows_by_date_keys":[str(k) for k in rows_by_date.keys()],"rows_by_date_counts":{str(k):len(v) for k,v in rows_by_date.items()},"skipped_games_count":len(skipped_games)},"timestamp":int(datetime.now().timestamp()*1000)}) + '\n')
        # #endregion
        
        all_skipped_games.extend(skipped_games)

        for game_date, rows in rows_by_date.items():
            # Only process rows for the target date, not tomorrow
            if game_date != target_date:
                continue
                
            # #region agent log
            with open('/Users/Brett/kdata/kalshi_bets/.cursor/debug.log', 'a') as f:
                import json
                f.write(json.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"I","location":"oddsapi_client.py:461","message":"Processing rows_by_date entry","data":{"sport_name":sport_name,"game_date":str(game_date),"rows_count":len(rows)},"timestamp":int(datetime.now().timestamp()*1000)}) + '\n')
            # #endregion
            
            if not rows:
                # #region agent log
                with open('/Users/Brett/kdata/kalshi_bets/.cursor/debug.log', 'a') as f:
                    import json
                    f.write(json.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"I","location":"oddsapi_client.py:464","message":"No rows for date, skipping","data":{"sport_name":sport_name,"game_date":str(game_date)},"timestamp":int(datetime.now().timestamp()*1000)}) + '\n')
                # #endregion
                continue

            # Separate by market type
            df = pd.DataFrame(rows)
            for market_type in df["market"].unique():
                market_data = df[df["market"] == market_type].to_dict("records")  # type: ignore
                
                # #region agent log
                with open('/Users/Brett/kdata/kalshi_bets/.cursor/debug.log', 'a') as f:
                    import json
                    f.write(json.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"I","location":"oddsapi_client.py:471","message":"Processing market type","data":{"sport_name":sport_name,"game_date":str(game_date),"market_type":market_type,"market_data_count":len(market_data)},"timestamp":int(datetime.now().timestamp()*1000)}) + '\n')
                # #endregion
                
                # Use sport name only (not sport_league) for filename
                key = f"{sport_name}_{market_type}"
                
                if key not in collected_data:
                    collected_data[key] = []
                collected_data[key].extend(market_data)

                # Save to file (only for the target date, not tomorrow)
                filename = f"{key.lower()}.csv"
                filepath = output_dir / filename
                
                # #region agent log
                with open('/Users/Brett/kdata/kalshi_bets/.cursor/debug.log', 'a') as f:
                    import json
                    f.write(json.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"I","location":"oddsapi_client.py:489","message":"Saving market data","data":{"sport_name":sport_name,"key":key,"filename":filename,"filepath":str(filepath),"market_data_count":len(market_data)},"timestamp":int(datetime.now().timestamp()*1000)}) + '\n')
                # #endregion
                
                save_market_data(market_data, filepath, market_type)

        # Save skipped games to CSV (one file per day, in data_collection directory)
        if all_skipped_games:
            # Group skipped games by date
            skipped_by_date = {}
            for skipped in all_skipped_games:
                # Try to extract date from commence_time
                commence_time = skipped.get("commence_time", "")
                if commence_time:
                    try:
                        # Parse the commence_time string to get date
                        dt = _as_cst_datetime(commence_time)
                        skip_date = dt.date()
                    except:
                        # If we can't parse, use target_date
                        skip_date = target_date
                else:
                    skip_date = target_date
                
                skipped_by_date.setdefault(skip_date, []).extend([skipped])
            
            # Save skipped games for each date in skipped_games subdirectory
            # Only save skipped games for the target date
            skipped_dir = output_dir.parent / "skipped_games"  # data_collection/data_curr/skipped_games
            skipped_dir.mkdir(parents=True, exist_ok=True)
            for skip_date, skipped_list in skipped_by_date.items():
                # Only save skipped games for the target date
                if skip_date == target_date:
                    date_str = skip_date.strftime("%Y-%m-%d")
                    skipped_filepath = skipped_dir / f"skipped_games_{date_str}.csv"
                    save_skipped_games(skipped_list, skipped_filepath)
                    print(f"📝 Saved {len(skipped_list)} skipped games to {skipped_filepath.name}")

    # Collect Kalshi markets (non-sports: by market)
    # This would require event ticker discovery, which is handled in the main loop
    # For now, we just log that Kalshi data collection happens during main loop

    return collected_data


def collect_data_standalone(output_dir: Optional[Path] = None, target_date: Optional[date] = None):
    """Collect market data when algorithm is not running.
    
    This is a standalone function that can be called independently.
    
    Args:
        output_dir: Optional output directory (default: DATA_DIR / target_date)
        target_date: Optional target date (default: today)
    """
    return collect_data_running(output_dir, target_date)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Collect market data from OddsAPI")
    parser.add_argument(
        "--date",
        type=str,
        help="Target date in YYYY-MM-DD format (default: today). Output directory will be automatically set to data_collection/data_curr/{date}"
    )
    args = parser.parse_args()
    
    target_date = None
    if args.date:
        try:
            target_date = datetime.strptime(args.date, "%Y-%m-%d").date()
        except ValueError:
            print(f"❌ Invalid date format: {args.date}. Expected YYYY-MM-DD")
            sys.exit(1)
    
    # output_dir will be automatically set in collect_data_running based on target_date
    collect_data_standalone(None, target_date)