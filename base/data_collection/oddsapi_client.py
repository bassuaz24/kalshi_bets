"""
OddsAPI client for fetching market data.
Data is separated by league for sports, by market otherwise.
"""

import os
import requests
import pandas as pd
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
import pytz
from pathlib import Path
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
        if resp.status_code != 200:
            print(f"❌ Error fetching {sport_key}: {resp.status_code} - {resp.text[:200]}")
            return None
        return resp.json()
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


def normalize_odds_data(sport_name: str, games: List[Dict[str, Any]], target_dates: set) -> Dict[str, List[Dict[str, Any]]]:
    """Normalize OddsAPI data to rows organized by date and market."""
    rows_by_date = {}

    for game in games:
        game_time = game.get("commence_time")
        if not game_time:
            continue
        try:
            game_time_cst = _as_cst_datetime(game_time)
        except Exception:
            continue
        game_date = game_time_cst.date()
        if game_date not in target_dates:
            continue

        home_team = game.get("home_team")
        away_team = game.get("away_team")
        game_id = game.get("id")

        for bookmaker in game.get("bookmakers", []):
            book_name = bookmaker["title"]
            for market in bookmaker.get("markets", []):
                market_type = market.get("key")

                for outcome in market.get("outcomes", []):
                    price = outcome.get("price")
                    if price is None:
                        continue

                    rows_by_date.setdefault(game_date, []).append({
                        "sport": sport_name,
                        "league": game.get("sport_title", ""),
                        "game_id": game_id,
                        "start_time": convert_to_cst(game_time_cst),
                        "bookmaker": book_name,
                        "market": market_type,
                        "team": outcome.get("name"),
                        "price": outcome.get("price"),
                        "point": outcome.get("point", None),
                        "home_team": home_team,
                        "away_team": away_team
                    })

    return rows_by_date


def save_market_data(data: List[Dict[str, Any]], filepath: Path, market_type: Optional[str] = None):
    """Save market data to CSV file."""
    if not data:
        return

    os.makedirs(filepath.parent, exist_ok=True)
    df = pd.DataFrame(data)
    
    columns = [
        "sport", "league", "game_id", "start_time",
        "bookmaker", "market", "team", "price", "point",
        "home_team", "away_team"
    ]
    
    # Only include columns that exist in the data
    existing_columns = [col for col in columns if col in df.columns]
    df = df[existing_columns]
    
    df.to_csv(filepath, index=False)


def collect_data_running(output_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Collect market data when algorithm is running.
    
    Returns:
        Dictionary with collected data organized by league/market.
    """
    if output_dir is None:
        output_dir = settings.DATA_DIR / datetime.now(CST).strftime("%Y-%m-%d")
    output_dir.mkdir(parents=True, exist_ok=True)

    today_cst = datetime.now(CST)
    today_date = today_cst.date()
    tomorrow_date = (today_cst + timedelta(days=1)).date()
    target_dates = {today_date, tomorrow_date}

    collected_data = {}

    # Collect from OddsAPI (sports data by league)
    for sport_name, sport_key in settings.SPORT_KEYS.items():
        print(f"📡 Fetching odds for {sport_name} ({sport_key})...")
        data = fetch_odds(sport_key)
        if not data:
            continue

        rows_by_date = normalize_odds_data(sport_name, data, target_dates)

        for game_date, rows in rows_by_date.items():
            if not rows:
                continue

            # Separate by market type
            df = pd.DataFrame(rows)
            for market_type in df["market"].unique():
                market_data = df[df["market"] == market_type].to_dict("records")
                
                # For sports: organize by league
                if settings.DATA_SEPARATE_BY_LEAGUE:
                    league = market_data[0].get("league", "unknown")
                    key = f"{sport_name}_{league}_{market_type}"
                else:
                    key = f"{sport_name}_{market_type}"
                
                if key not in collected_data:
                    collected_data[key] = []
                collected_data[key].extend(market_data)

                # Save to file
                date_str = game_date.strftime("%Y-%m-%d")
                suffix = "2" if game_date == tomorrow_date else ""
                filename = f"{key.lower()}{suffix}.csv"
                filepath = output_dir / filename
                save_market_data(market_data, filepath, market_type)

    # Collect Kalshi markets (non-sports: by market)
    # This would require event ticker discovery, which is handled in the main loop
    # For now, we just log that Kalshi data collection happens during main loop

    return collected_data


def collect_data_standalone(output_dir: Optional[Path] = None):
    """Collect market data when algorithm is not running.
    
    This is a standalone function that can be called independently.
    """
    return collect_data_running(output_dir)