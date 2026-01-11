"""
Market discovery logic to match OddsAPI events with Kalshi markets.

This module implements a scalable approach to discover tradable markets by:
1. Fetching events from OddsAPI
2. Matching them to Kalshi event tickers (via construction or API lookup)
3. Fetching Kalshi markets for matched events
4. Filtering for active, liquid markets suitable for trading
5. Returning structured match data for the strategy engine

IMPROVEMENTS:
- Proper team name mapping using smart_team_lookup
- Match caching to avoid redundant API calls
- Enhanced fuzzy matching for better team name resolution
- Better ticker construction using myles_repo approach
- Support for Kalshi exchange API (if available)
"""

from typing import List, Dict, Any, Optional, Tuple, Set
from datetime import datetime, timedelta
import time
import re
from dataclasses import dataclass
from config import settings
from data_collection.oddsapi_client import fetch_odds, normalize_odds_data
from kalshi.markets import get_kalshi_markets, get_event_total_volume
from core.time import now_utc
from strategy.utils import smart_team_lookup, fuzzy_match_teams
from strategy.match_cache import get_match_cache
from data.team_maps import TEAM_MAP, NBA_TEAM_MAP
import pytz

CST = pytz.timezone("America/Chicago")


@dataclass
class MarketMatch:
    """Represents a matched market between OddsAPI and Kalshi."""
    event_ticker: str  # Kalshi event ticker (e.g., "KXNBAGAME-09JAN24-AWAY-HOME")
    match_description: str  # Human-readable match (e.g., "Lakers vs Warriors")
    home_team: str
    away_team: str
    start_time: datetime
    kalshi_markets: List[Dict[str, Any]]  # List of active Kalshi markets
    odds_data: Dict[str, Any]  # OddsAPI data for this event
    total_volume: Optional[int]  # Total trading volume for the event


def make_ncaa_event_ticker(home_team: str, away_team: str, event_date) -> List[str]:
    """
    Build Kalshi NCAA Basketball event ticker from home/away team names and date.
    Returns men's or women's ticker format based on whether team names contain "(W)".
    Uses smart_team_lookup for proper team code mapping.
    """
    if hasattr(event_date, "strftime"):
        date_code = event_date.strftime("%y%b%d").upper()
    else:
        date_code = str(event_date).upper()

    home_code, home_confidence, _ = smart_team_lookup(home_team, TEAM_MAP)
    away_code, away_confidence, _ = smart_team_lookup(away_team, TEAM_MAP)

    if not home_code:
        home_clean = re.sub(r"\s*\([WMwm]\)\s*", "", str(home_team)).strip()
        home_code = home_clean[:4].upper() if home_clean else home_team[:4].upper()
        home_confidence = "fallback"

    if not away_code:
        away_clean = re.sub(r"\s*\([WMwm]\)\s*", "", str(away_team)).strip()
        away_code = away_clean[:4].upper() if away_clean else away_team[:4].upper()
        away_confidence = "fallback"

    if settings.VERBOSE and (home_confidence != "exact" or away_confidence != "exact"):
        print(
            f"   🔍 Team matching: {home_team} → {home_code} ({home_confidence}), "
            f"{away_team} → {away_code} ({away_confidence})"
        )

    is_womens = "(W)" in str(home_team) or "(W)" in str(away_team)
    if is_womens:
        ticker_womens = f"KXNCAAWBGAME-{date_code}{away_code}{home_code}"
        return [ticker_womens]
    ticker_mens = f"KXNCAAMBGAME-{date_code}{away_code}{home_code}"
    return [ticker_mens]


def make_nba_event_ticker(home_team: str, away_team: str, event_date) -> List[str]:
    """Build Kalshi NBA Basketball event ticker from home/away team names and date."""
    if hasattr(event_date, "strftime"):
        date_code = event_date.strftime("%y%b%d").upper()
    else:
        date_code = str(event_date).upper()

    def normalize_nba_team_name(team_name: str) -> str:
        if not team_name:
            return ""
        normalized = team_name.lower().strip()
        normalized = re.sub(r"\s*\([^)]*\)\s*", " ", normalized)
        normalized = re.sub(r"\s+", " ", normalized).strip()
        return normalized

    home_normalized = normalize_nba_team_name(home_team)
    away_normalized = normalize_nba_team_name(away_team)

    home_code = NBA_TEAM_MAP.get(home_normalized)
    away_code = NBA_TEAM_MAP.get(away_normalized)

    if not home_code:
        home_words = home_normalized.split()
        for word in home_words:
            if word in NBA_TEAM_MAP:
                home_code = NBA_TEAM_MAP[word]
                break
        if not home_code:
            for key, code in NBA_TEAM_MAP.items():
                if key in home_normalized or home_normalized in key:
                    home_code = code
                    break

    if not away_code:
        away_words = away_normalized.split()
        for word in away_words:
            if word in NBA_TEAM_MAP:
                away_code = NBA_TEAM_MAP[word]
                break
        if not away_code:
            for key, code in NBA_TEAM_MAP.items():
                if key in away_normalized or away_normalized in key:
                    away_code = code
                    break

    if not home_code:
        home_clean = re.sub(r"[^a-z]", "", home_normalized)
        home_code = home_clean[:3].upper() if len(home_clean) >= 3 else (home_clean + "x" * (3 - len(home_clean))).upper()
        if settings.VERBOSE:
            print(f"   ⚠️ NBA team code fallback: {home_team} → {home_code}")

    if not away_code:
        away_clean = re.sub(r"[^a-z]", "", away_normalized)
        away_code = away_clean[:3].upper() if len(away_clean) >= 3 else (away_clean + "x" * (3 - len(away_clean))).upper()
        if settings.VERBOSE:
            print(f"   ⚠️ NBA team code fallback: {away_team} → {away_code}")

    home_code = home_code[:3].upper() if home_code else "XXX"
    away_code = away_code[:3].upper() if away_code else "XXX"

    ticker = f"KXNBAGAME-{date_code}{away_code}{home_code}"
    return [ticker]


def construct_kalshi_event_ticker(
    sport_key: str,
    home_team: str,
    away_team: str,
    event_date: datetime
) -> List[str]:
    """
    Construct potential Kalshi event tickers from sport, teams, and date.
    
    Returns list of candidate tickers to try (in order of preference).
    Uses proper team mapping for accurate ticker construction.
    """
    # Use specialized functions for NBA and NCAA
    if sport_key == "basketball_nba":
        return make_nba_event_ticker(home_team, away_team, event_date)
    elif sport_key in ("basketball_ncaab", "basketball_wncaab"):
        return make_ncaa_event_ticker(home_team, away_team, event_date)
    
    # For NFL and CFB, use simple construction with smart_team_lookup
    if hasattr(event_date, "strftime"):
        date_code = event_date.strftime("%y%b%d").upper()
    else:
        date_code = str(event_date).upper()
    
    series_map = {
        "americanfootball_nfl": "KXNFLGAME",
        "americanfootball_ncaaf": "KXNCAAFGAME",
    }
    
    series = series_map.get(sport_key)
    if not series:
        return []
    
    # Use smart_team_lookup for proper team codes
    home_code, _, _ = smart_team_lookup(home_team, TEAM_MAP)
    away_code, _, _ = smart_team_lookup(away_team, TEAM_MAP)
    
    # Fallback if lookup fails
    if not home_code:
        home_clean = re.sub(r"\s*\([WMwm]\)\s*", "", str(home_team)).strip()
        home_code = home_clean[:4].upper() if home_clean else home_team[:4].upper()
    
    if not away_code:
        away_clean = re.sub(r"\s*\([WMwm]\)\s*", "", str(away_team)).strip()
        away_code = away_clean[:4].upper() if away_clean else away_team[:4].upper()
    
    # Generate candidates (try both team orders)
    candidates = [
        f"{series}-{date_code}{away_code}{home_code}",
        f"{series}-{date_code}{home_code}{away_code}",
    ]
    
    return candidates


def match_oddsapi_to_kalshi(
    oddsapi_events: List[Dict[str, Any]],
    sport_key: str
) -> List[MarketMatch]:
    """
    Match OddsAPI events to Kalshi event tickers with caching.
    
    Strategy:
    1. For each OddsAPI event, construct candidate Kalshi tickers using proper team mapping
    2. Check cache first to avoid redundant API calls
    3. Try each ticker by fetching markets from Kalshi
    4. Cache successful matches
    5. If markets found, create a MarketMatch
    6. Filter by volume/liquidity requirements
    
    Args:
        oddsapi_events: List of events from OddsAPI
        sport_key: Sport key (e.g., "basketball_nba")
    
    Returns:
        List of MarketMatch objects
    """
    matches = []
    cache = get_match_cache()
    
    # Clear expired cache entries periodically
    cache.clear_expired()
    
    for event in oddsapi_events:
        # Extract event data
        home_team = event.get("home_team") or (event.get("home") or {}).get("name", "")
        away_team = event.get("away_team") or (event.get("away") or {}).get("name", "")
        commence_time = event.get("commence_time")
        
        if not home_team or not away_team or not commence_time:
            if settings.VERBOSE:
                print(f"   ⚠️ Skipping event (missing data): {home_team} vs {away_team}")
            continue
        
        # Parse start time
        try:
            if isinstance(commence_time, str):
                dt = datetime.fromisoformat(commence_time.replace("Z", "+00:00"))
            elif isinstance(commence_time, (int, float)):
                dt = datetime.fromtimestamp(commence_time, tz=pytz.UTC)
            else:
                dt = commence_time
            if dt.tzinfo is None:
                dt = pytz.UTC.localize(dt)
        except Exception as e:
            if settings.VERBOSE:
                print(f"   ⚠️ Could not parse start time for {home_team} vs {away_team}: {e}")
            continue
        
        # Create cache key
        cache_key = f"{sport_key}:{home_team}:{away_team}:{dt.strftime('%Y-%m-%d')}"
        
        # Check cache first
        cached_match = cache.get(cache_key)
        if cached_match:
            matched_ticker = cached_match.event_ticker
            matched_markets = cached_match.markets
            if settings.VERBOSE:
                print(f"   💾 Cache hit for {home_team} vs {away_team}: {matched_ticker}")
        else:
            # Construct candidate tickers using proper team mapping
            candidates = construct_kalshi_event_ticker(sport_key, home_team, away_team, dt)
            
            if not candidates:
                if settings.VERBOSE:
                    print(f"   ⚠️ No ticker candidates for {sport_key}: {away_team} vs {home_team}")
                continue
            
            # Try each candidate ticker
            matched_ticker = None
            matched_markets = None
            
            for ticker in candidates:
                # Rate limiting: small delay between attempts
                time.sleep(0.2)
                
                markets = get_kalshi_markets(ticker, force_live=True)
                
                if markets is None:
                    # Rate limited - wait and retry once
                    if settings.VERBOSE:
                        print(f"      ⚠️ Rate limited for {ticker}, waiting 1s...")
                    time.sleep(1.0)
                    markets = get_kalshi_markets(ticker, force_live=True)
                    if markets is None:
                        # Still rate limited - skip remaining candidates for this event
                        if settings.VERBOSE:
                            print(f"      ⚠️ Still rate limited, skipping remaining tickers")
                        break
                
                if markets:
                    # Found markets! Use this ticker
                    matched_ticker = ticker
                    matched_markets = markets
                    # Cache the match
                    cache.set(cache_key, matched_ticker, matched_markets)
                    if settings.VERBOSE:
                        print(f"      ✅ Found {len(markets)} markets for {ticker} (cached)")
                    break
            
            if not matched_ticker or not matched_markets:
                if settings.VERBOSE:
                    print(f"   ⚠️ No Kalshi markets found for {away_team} vs {home_team}")
                continue
        
        # Check volume/liquidity
        total_volume = get_event_total_volume(matched_ticker, matched_markets)
        
        # Filter by minimum volume (if configured)
        min_volume = settings.MIN_TRADING_VOLUME_PER_EVENT
        if min_volume > 0 and (total_volume is None or total_volume < min_volume):
            if settings.VERBOSE:
                print(f"   ⚠️ Skipping {matched_ticker} - volume {total_volume} < {min_volume}")
            continue
        
        # Create match object
        match_description = f"{away_team} vs {home_team}"
        
        match = MarketMatch(
            event_ticker=matched_ticker,
            match_description=match_description,
            home_team=home_team,
            away_team=away_team,
            start_time=dt,
            kalshi_markets=matched_markets,
            odds_data=event,
            total_volume=total_volume,
        )
        
        matches.append(match)
        
        if settings.VERBOSE:
            print(f"   ✅ Matched: {match_description} → {matched_ticker} ({len(matched_markets)} markets, volume: {total_volume})")
    
    return matches


def discover_markets(
    target_dates: Optional[Set[datetime.date]] = None,
    sport_keys: Optional[List[str]] = None
) -> List[Dict[str, Any]]:
    """
    Main market discovery function.
    
    Discovers active markets by:
    1. Fetching events from OddsAPI for specified sports
    2. Matching to Kalshi event tickers
    3. Fetching Kalshi markets for matched events
    4. Filtering and structuring results
    
    Args:
        target_dates: Set of dates to look for events (default: today + tomorrow)
        sport_keys: List of sport keys to search (default: all configured sports)
    
    Returns:
        List of match dictionaries in format expected by strategy engine:
        {
            "ticker": event_ticker,
            "match": match_description,
            "home": home_team,
            "away": away_team,
            "kalshi": [list of market dicts],
            "odds_feed": odds_data,
            "start_time": datetime,
        }
    """
    if target_dates is None:
        today_cst = datetime.now(CST)
        target_dates = {
            today_cst.date(),
            (today_cst + timedelta(days=1)).date()
        }
    
    if sport_keys is None:
        sport_keys = list(settings.SPORT_KEYS.values())
    
    all_matches = []
    
    # Process each sport
    for sport_name, sport_key in settings.SPORT_KEYS.items():
        if sport_key not in sport_keys:
            continue
        
        print(f"🔍 Discovering markets for {sport_name} ({sport_key})...")
        
        # Fetch from OddsAPI
        oddsapi_data = fetch_odds(sport_key)
        if not oddsapi_data:
            continue
        
        # Filter events by target dates
        filtered_events = []
        for event in oddsapi_data:
            commence_time = event.get("commence_time")
            if not commence_time:
                continue
            
            try:
                if isinstance(commence_time, str):
                    dt = datetime.fromisoformat(commence_time.replace("Z", "+00:00"))
                elif isinstance(commence_time, (int, float)):
                    dt = datetime.fromtimestamp(commence_time, tz=pytz.UTC)
                else:
                    dt = commence_time
                if dt.tzinfo is None:
                    dt = pytz.UTC.localize(dt)
                
                event_date = dt.astimezone(CST).date()
                if event_date in target_dates:
                    filtered_events.append(event)
            except Exception:
                continue
        
        if not filtered_events:
            if settings.VERBOSE:
                print(f"   ⚠️ No events found for {sport_name} on target dates")
            continue
        
        print(f"   📊 Found {len(filtered_events)} OddsAPI events for {sport_name}")
        
        # Match to Kalshi
        matches = match_oddsapi_to_kalshi(filtered_events, sport_key)
        
        # Convert to strategy engine format
        for match in matches:
            match_dict = {
                "ticker": match.event_ticker,
                "match": match.match_description,
                "home": match.home_team,
                "away": match.away_team,
                "kalshi": match.kalshi_markets,
                "odds_feed": match.odds_data,
                "start_time": match.start_time,
                "total_volume": match.total_volume,
            }
            all_matches.append(match_dict)
        
        print(f"   ✅ Matched {len(matches)} events to Kalshi for {sport_name}")
    
    print(f"🎯 Total market discoveries: {len(all_matches)}")
    return all_matches
