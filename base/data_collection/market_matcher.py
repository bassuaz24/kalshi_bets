"""
Market matching system for combining Kalshi and OddsAPI data.

This module handles:
- Parsing Kalshi tickers to extract sport, market type, date, and team codes
- Matching Kalshi markets to OddsAPI data files
- Computing weighted averages of OddsAPI prices
- Storing matches in a persistent cache
- Writing joined CSV files with combined data
"""

import os
import sys
import re
import json
import pandas as pd
from typing import Dict, List, Optional, Tuple, Any, Set
from datetime import date, datetime
from pathlib import Path
from collections import defaultdict

# Add base directory to path
_BASE_ROOT = Path(__file__).parent.parent.absolute()
if str(_BASE_ROOT) not in sys.path:
    sys.path.insert(0, str(_BASE_ROOT))

from config import settings
from data.team_map import TEAM_MAP_B
from strategy.utils import normalize_team_name, fuzzy_match_teams


# Sport key mappings (Kalshi sport code -> OddsAPI sport key)
KALSHI_TO_ODDSAPI_SPORT = {
    "NFL": "americanfootball_nfl",
    "NBA": "basketball_nba",
    "CBBM": "basketball_ncaab",
    "CBBW": "basketball_wncaab",

}

# Market type mappings (Kalshi series label -> OddsAPI market type)
MARKET_TYPE_MAP = {
    "GAME": "h2h",
    "SPREAD": "spreads",
    "TOTAL": "totals",
}

# Reverse map: normalized OddsAPI team name -> ticker code
_REVERSE_TEAM_MAP: Optional[Dict[str, str]] = None


def _build_reverse_team_map() -> Dict[str, str]:
    """Build reverse map from TEAM_MAP_B: normalized name -> ticker code."""
    global _REVERSE_TEAM_MAP
    if _REVERSE_TEAM_MAP is not None:
        return _REVERSE_TEAM_MAP
    
    _REVERSE_TEAM_MAP = {}
    for ticker, full_name in TEAM_MAP_B.items():
        normalized = normalize_team_name(full_name)
        if normalized:
            _REVERSE_TEAM_MAP[normalized] = ticker
    
    return _REVERSE_TEAM_MAP


def ticker_to_team_name(ticker_code: str, sport: str) -> Optional[str]:
    """
    Convert Kalshi ticker code to full team name using TEAM_MAP_B.
    
    Args:
        ticker_code: Kalshi ticker code (e.g., "OAK")
        sport: Sport code (e.g., "CBBM", "NBA") - not used, kept for compatibility
    
    Returns:
        Full team name from TEAM_MAP_B, or None if not found
    """
    ticker_upper = ticker_code.upper()
    ticker_lower = ticker_code.lower()
    
    # Use TEAM_MAP_B for all sports (includes NBA and NCAA teams)
    # Try both uppercase and lowercase keys
    result = TEAM_MAP_B.get(ticker_upper)
    if result is None:
        result = TEAM_MAP_B.get(ticker_lower)
    return result


def parse_kalshi_ticker(ticker: str) -> Optional[Dict[str, Any]]:
    """
    Parse Kalshi ticker to extract sport, market type, date, and team codes.
    
    Examples:
        KXNCAAMBGAME-26JAN15OAKMILW-OAK -> {
            "sport": "CBBM",
            "market_type": "GAME",
            "date": date(2026, 1, 15),
            "team_code": "OAK"
        }
        KXNCAAMBSPREAD-26JAN15IDHOIDST-IDST6 -> {
            "sport": "CBBM",
            "market_type": "SPREAD",
            "date": date(2026, 1, 15),
            "team_code": "IDST",
            "spread": 6
        }
        KXNCAAMBTOTAL-26JAN15WICHFAU-137 -> {
            "sport": "CBBM",
            "market_type": "TOTAL",
            "date": date(2026, 1, 15),
            "away_team_code": "WICH",
            "home_team_code": "FAU",
            "total": 137
        }
    
    Returns:
        Dict with parsed information, or None if parsing fails
    """
    ticker_upper = ticker.upper()
    
    # Extract series prefix (e.g., KXNCAAMBGAME, KXNCAAMBSPREAD)
    series_match = re.match(r"^(KX[A-Z]+)(GAME|SPREAD|TOTAL)", ticker_upper)
    if not series_match:
        return None
    
    series_prefix = series_match.group(1)
    market_type = series_match.group(2)
    
    # Map series prefix to sport
    sport = None
    if series_prefix.startswith("KXNFL"):
        sport = "NFL"
    elif series_prefix.startswith("KXNBA"):
        sport = "NBA"
    elif series_prefix.startswith("KXNCAAM"):
        sport = "CBBM"
    elif series_prefix.startswith("KXNCAAW"):
        sport = "CBBW"
    elif series_prefix.startswith("KXNCAAF"):
        sport = "CFB"
    
    if not sport:
        return None
    
    # Extract date from ticker (format: -26JAN15)
    date_match = re.search(r"-(\d{2}[A-Z]{3}\d{2})", ticker_upper)
    if not date_match:
        return None
    
    try:
        event_date = datetime.strptime(date_match.group(1), "%y%b%d").date()
    except ValueError:
        return None
    
    result = {
        "sport": sport,
        "market_type": market_type,
        "date": event_date,
        "ticker": ticker_upper,
    }
    
    # Parse based on market type
    if market_type == "GAME":
        # Format: KXNCAAMBGAME-26JAN15OAKMILW-OAK
        # Extract team code from the end (after last dash)
        parts = ticker_upper.split("-")
        if len(parts) >= 2:
            team_code = parts[-1]
            result["team_code"] = team_code
    elif market_type == "SPREAD":
        # Format: KXNCAAMBSPREAD-26JAN15IDHOIDST-IDST6
        # Extract team code and spread number from the end
        parts = ticker_upper.split("-")
        if len(parts) >= 2:
            end_part = parts[-1]
            # Find the spread number (digits at the end)
            spread_match = re.search(r"(\d+)$", end_part)
            if spread_match:
                spread_num = int(spread_match.group(1))
                # Extract team code (everything before the digits)
                team_code = end_part[: -len(spread_match.group(1))]
                result["team_code"] = team_code
                result["spread"] = spread_num
    elif market_type == "TOTAL":
        # Format: KXNCAAMBTOTAL-26JAN15WICHFAU-137
        # Extract away/home team codes and total number
        parts = ticker_upper.split("-")
        if len(parts) >= 2:
            # The middle part contains team codes: 26JAN15WICHFAU
            middle_part = parts[-2] if len(parts) >= 3 else parts[0]
            # Extract date part and team codes
            date_str = date_match.group(1)
            date_len = len(date_str)
            # Find where date ends and team codes begin
            date_end_idx = middle_part.find(date_str) + date_len if date_str in middle_part else date_len
            team_part = middle_part[date_end_idx:]
            
            # Extract total number from last part
            total_match = re.search(r"(\d+)$", parts[-1])
            if total_match:
                total_num = int(total_match.group(1))
                result["total"] = total_num
                
                # Parse team codes: the end of ticker shows the full/accurate team code (home team)
                # Format: WICHFAU where last 2-4 chars are home team, rest is away team
                # Try to identify home team code from the end by matching known team codes
                home_code = None
                away_code = None
                
                # Try 4-char, 3-char, and 2-char codes from the end
                for code_len in [4, 3, 2]:
                    if len(team_part) >= code_len:
                        potential_home = team_part[-code_len:]
                        # Check if this matches a known team code
                        if ticker_to_team_name(potential_home, sport):
                            home_code = potential_home
                            away_code = team_part[:-code_len]
                            break
                
                # If we couldn't identify, try splitting in the middle
                if not home_code and len(team_part) >= 6:
                    # Try common splits: 3-3, 3-4, 4-3, 4-4
                    for away_len in [3, 4]:
                        for home_len in [3, 4]:
                            if away_len + home_len == len(team_part):
                                potential_away = team_part[:away_len]
                                potential_home = team_part[away_len:]
                                # Prefer the split where home team is recognizable
                                if ticker_to_team_name(potential_home, sport):
                                    away_code = potential_away
                                    home_code = potential_home
                                    break
                        if home_code:
                            break
                
                # Fallback: assume equal split with home at end
                if not home_code:
                    split_point = len(team_part) // 2
                    away_code = team_part[:split_point]
                    home_code = team_part[split_point:]
                
                if away_code and home_code:
                    result["away_team_code"] = away_code
                    result["home_team_code"] = home_code
    
    return result


def get_oddsapi_file_path(sport: str, market_type: str, event_date: date, data_dir: Path) -> Optional[Path]:
    """
    Get the path to the OddsAPI CSV file for a given sport, market type, and date.
    
    Args:
        sport: Sport code (e.g., "CBBM")
        market_type: Market type ("GAME", "SPREAD", "TOTAL")
        event_date: Event date
        data_dir: Base data directory
    
    Returns:
        Path to CSV file, or None if not found
    """
    # Map sport to OddsAPI sport key
    oddsapi_sport = KALSHI_TO_ODDSAPI_SPORT.get(sport)
    if not oddsapi_sport:
        return None
    
    # Map market type to OddsAPI market type
    oddsapi_market = MARKET_TYPE_MAP.get(market_type)
    if not oddsapi_market:
        return None
    
    # Build file path: data_dir/YYYY-MM-DD/{sport}_{market}.csv
    date_str = event_date.isoformat()
    date_dir = data_dir / date_str
    
    # File naming: sport name lowercase + market type
    sport_name_lower = sport.lower()
    filename = f"{sport_name_lower}_{oddsapi_market}.csv"
    
    file_path = date_dir / filename
    
    # Only return if file exists (don't use "2" suffix files)
    if file_path.exists():
        return file_path
    
    return None


def load_oddsapi_data(file_path: Path, normalize: bool = True) -> pd.DataFrame:
    """Load OddsAPI CSV file into DataFrame.
    
    Args:
        file_path: Path to CSV file
        normalize: If True, pre-normalize team names for faster matching (default: True)
    """
    if not file_path.exists():
        return pd.DataFrame()
    
    try:
        df = pd.read_csv(file_path)
        
        # Pre-normalize team names for faster matching
        if normalize and len(df) > 0:
            if "team" in df.columns:
                df["team_normalized"] = df["team"].astype(str).apply(
                    lambda x: normalize_team_name(str(x).strip())
                )
            if "away_team" in df.columns:
                df["away_team_normalized"] = df["away_team"].astype(str).apply(
                    lambda x: normalize_team_name(str(x).strip())
                )
            if "home_team" in df.columns:
                df["home_team_normalized"] = df["home_team"].astype(str).apply(
                    lambda x: normalize_team_name(str(x).strip())
                )
        
        return df
    except Exception as e:
        print(f"⚠️ Error loading OddsAPI file {file_path}: {e}")
        return pd.DataFrame()


def match_h2h_market(
    ticker: str,
    parsed: Dict[str, Any],
    oddsapi_df: pd.DataFrame,
    sport: str
) -> Optional[str]:
    """
    Match H2H (GAME) market to OddsAPI data.
    
    Returns:
        Match key in format "game_id|team" or None if no match
    """
    team_code = parsed.get("team_code")
    if not team_code:
        return None
    
    # Convert ticker code to team name
    team_name = ticker_to_team_name(team_code, sport)
    if not team_name:
        return None
    
    # Normalize team name for matching
    normalized_team = normalize_team_name(team_name)
    
    # Use vectorized pandas operations instead of iterrows()
    # First try exact match on normalized team names
    if "team_normalized" not in oddsapi_df.columns:
        # Pre-normalize team names if not already done
        oddsapi_df = oddsapi_df.copy()
        oddsapi_df["team_normalized"] = oddsapi_df["team"].astype(str).apply(
            lambda x: normalize_team_name(str(x).strip())
        )
    
    # Filter for exact matches
    exact_matches = oddsapi_df[oddsapi_df["team_normalized"] == normalized_team]
    
    if len(exact_matches) > 0:
        match_row = exact_matches.iloc[0]
        game_id = str(match_row.get("game_id", ""))
        matched_team = str(match_row.get("team", ""))
        # Use | as delimiter to handle team names with dashes
        return f"{game_id}|{matched_team}"
    
    # Fallback to fuzzy matching (only if exact match fails)
    # This is slower, so we only do it if needed
    for row in oddsapi_df.itertuples(index=False):
        row_team = str(getattr(row, "team", "")).strip()
        if row_team and fuzzy_match_teams(row_team, team_name):
            game_id = str(getattr(row, "game_id", ""))
            matched_team = str(getattr(row, "team", ""))
            # Use | as delimiter to handle team names with dashes
            return f"{game_id}|{matched_team}"
    
    return None


def match_spread_market(
    ticker: str,
    parsed: Dict[str, Any],
    oddsapi_df: pd.DataFrame,
    sport: str
) -> Optional[str]:
    """
    Match SPREAD market to OddsAPI data.
    
    Returns:
        Match key in format "game_id-team-point" or None if no match
    """
    team_code = parsed.get("team_code")
    spread_num = parsed.get("spread")
    
    if not team_code or spread_num is None:
        return None
    
    # Transform spread: add 0.5, then multiply by -1
    transformed_spread = (spread_num + 0.5) * -1
    
    # Convert ticker code to team name
    team_name = ticker_to_team_name(team_code, sport)
    if not team_name:
        return None
    
    normalized_team = normalize_team_name(team_name)
    
    # Use pre-normalized team names (should already be in dataframe from load_oddsapi_data)
    if "team_normalized" not in oddsapi_df.columns:
        # Fallback: normalize on the fly (shouldn't happen if cache is working)
        oddsapi_df["team_normalized"] = oddsapi_df["team"].astype(str).apply(
            lambda x: normalize_team_name(str(x).strip())
        )
    
    # Filter for point matches first (numeric comparison is fast)
    point_matches = oddsapi_df[
        (pd.notna(oddsapi_df["point"])) &
        (abs(oddsapi_df["point"].astype(float) - transformed_spread) < 0.01)
    ]
    
    if len(point_matches) == 0:
        return None
    
    # Then filter for team matches (exact match first)
    exact_team_matches = point_matches[point_matches["team_normalized"] == normalized_team]
    
    if len(exact_team_matches) > 0:
        match_row = exact_team_matches.iloc[0]
        game_id = str(match_row.get("game_id", ""))
        matched_team = str(match_row.get("team", ""))
        point = float(match_row.get("point", 0))
        # Use | as delimiter to avoid issues with negative point values
        return f"{game_id}|{matched_team}|{point}"
    
    # Fallback to fuzzy matching (only if exact match fails)
    for row in point_matches.itertuples(index=False):
        row_team = str(getattr(row, "team", "")).strip()
        if row_team and fuzzy_match_teams(row_team, team_name):
            game_id = str(getattr(row, "game_id", ""))
            matched_team = str(getattr(row, "team", ""))
            point = float(getattr(row, "point", 0))
            # Use | as delimiter to avoid issues with negative point values
            return f"{game_id}|{matched_team}|{point}"
    
    return None


def match_total_market(
    ticker: str,
    parsed: Dict[str, Any],
    oddsapi_df: pd.DataFrame,
    sport: str
) -> Optional[str]:
    """
    Match TOTAL market to OddsAPI data.
    
    Returns:
        Match key in format "game_id-home_team-point-team" or None if no match
    """
    away_code = parsed.get("away_team_code")
    home_code = parsed.get("home_team_code")
    total_num = parsed.get("total")
    
    if not away_code or not home_code or total_num is None:
        return None
    
    # Transform total: add 0.5
    transformed_total = total_num + 0.5
    
    # Convert ticker codes to team names
    away_name = ticker_to_team_name(away_code, sport)
    home_name = ticker_to_team_name(home_code, sport)
    
    if not away_name or not home_name:
        return None
    
    normalized_away = normalize_team_name(away_name)
    normalized_home = normalize_team_name(home_name)
    
    # Use pre-normalized team names (should already be in dataframe from load_oddsapi_data)
    if "away_team_normalized" not in oddsapi_df.columns:
        # Fallback: normalize on the fly (shouldn't happen if cache is working)
        oddsapi_df["away_team_normalized"] = oddsapi_df["away_team"].astype(str).apply(
            lambda x: normalize_team_name(str(x).strip())
        )
        oddsapi_df["home_team_normalized"] = oddsapi_df["home_team"].astype(str).apply(
            lambda x: normalize_team_name(str(x).strip())
        )
    
    # Filter using vectorized operations
    # First filter for "Over" and point match (fast filters)
    filtered = oddsapi_df[
        (oddsapi_df["team"].astype(str).str.lower() == "over") &
        (pd.notna(oddsapi_df["point"])) &
        (abs(oddsapi_df["point"].astype(float) - transformed_total) < 0.01)
    ]
    
    if len(filtered) == 0:
        return None
    
    # Then filter for exact team matches
    exact_matches = filtered[
        (filtered["away_team_normalized"] == normalized_away) &
        (filtered["home_team_normalized"] == normalized_home)
    ]
    
    if len(exact_matches) > 0:
        match_row = exact_matches.iloc[0]
        game_id = str(match_row.get("game_id", ""))
        matched_home = str(match_row.get("home_team", ""))
        point = float(match_row.get("point", 0))
        team = str(match_row.get("team", ""))
        # Use | as delimiter to avoid issues with negative point values
        return f"{game_id}|{matched_home}|{point}|{team}"
    
    # Fallback to fuzzy matching (only if exact match fails)
    for row in filtered.itertuples(index=False):
        row_away = str(getattr(row, "away_team", "")).strip()
        row_home = str(getattr(row, "home_team", "")).strip()
        
        if row_away and row_home:
            away_matches = fuzzy_match_teams(row_away, away_name)
            home_matches = fuzzy_match_teams(row_home, home_name)
            
            if away_matches and home_matches:
                game_id = str(getattr(row, "game_id", ""))
                matched_home = str(getattr(row, "home_team", ""))
                point = float(getattr(row, "point", 0))
                team = str(getattr(row, "team", ""))
                # Use | as delimiter to avoid issues with negative point values
                return f"{game_id}|{matched_home}|{point}|{team}"
    
    return None


def compute_weighted_average(
    oddsapi_rows: List[pd.Series],
    weights: Dict[str, float]
) -> Optional[float]:
    """
    Compute weighted average of OddsAPI prices.
    
    Args:
        oddsapi_rows: List of DataFrame rows with "bookmaker" and "price" columns
        weights: Dict mapping bookmaker names to weights
    
    Returns:
        Weighted average price, or None if no valid prices
    """
    total_weight = 0.0
    weighted_sum = 0.0
    
    for row in oddsapi_rows:
        bookmaker = str(row.get("bookmaker", "")).strip()
        price = row.get("price")
        
        if pd.isna(price):
            continue
        
        # Find matching weight (case-insensitive)
        weight = None
        for book_name, book_weight in weights.items():
            if bookmaker.lower() == book_name.lower():
                weight = book_weight
                break
        
        if weight is None:
            # Use default weight if bookmaker not in weights
            continue
        
        try:
            price_float = float(price)
            weighted_sum += price_float * weight
            total_weight += weight
        except (ValueError, TypeError):
            continue
    
    if total_weight == 0:
        return None
    
    return weighted_sum / total_weight


class MarketMatcher:
    """Manages market matching between Kalshi and OddsAPI."""
    
    def __init__(self, data_dir: Path, match_cache_file: Optional[Path] = None):
        """
        Initialize market matcher.
        
        Args:
            data_dir: Base directory for OddsAPI data files
            match_cache_file: Path to JSON file for storing matches (optional)
        """
        self.data_dir = data_dir
        self.match_cache_file = match_cache_file
        self.matches: Dict[str, str] = {}  # kalshi_ticker -> match_key
        self.unmatched_tickers: Set[str] = set()
        self.match_stats = {
            "total_attempted": 0,
            "matched": 0,
            "unmatched": 0,
            "h2h_matched": 0,
            "spread_matched": 0,
            "total_matched": 0,
        }
        
        # Cache for loaded dataframes (keyed by file path)
        self._df_cache: Dict[str, pd.DataFrame] = {}
        
        # Load existing matches if cache file exists
        if self.match_cache_file and self.match_cache_file.exists():
            self._load_matches()
    
    def _load_matches(self):
        """Load matches from cache file."""
        try:
            with open(self.match_cache_file, 'r') as f:
                data = json.load(f)
                self.matches = data.get("matches", {})
                self.match_stats = data.get("stats", self.match_stats)
        except Exception as e:
            print(f"⚠️ Error loading match cache: {e}")
    
    def _save_matches(self):
        """Save matches to cache file."""
        if not self.match_cache_file:
            return
        
        try:
            os.makedirs(self.match_cache_file.parent, exist_ok=True)
            with open(self.match_cache_file, 'w') as f:
                json.dump({
                    "matches": self.matches,
                    "stats": self.match_stats,
                }, f, indent=2)
        except Exception as e:
            print(f"⚠️ Error saving match cache: {e}")
    
    def find_match(self, ticker: str, market: Dict[str, Any]) -> Optional[str]:
        """
        Find OddsAPI match for a Kalshi ticker.
        
        Args:
            ticker: Kalshi market ticker
            market: Kalshi market data dict
        
        Returns:
            Match key (e.g., "game_id-team") or None if no match
        """
        # Check cache first (already matched)
        if ticker in self.matches:
            return self.matches[ticker]
        
        # If already marked as unmatched, return None without incrementing counter
        if ticker in self.unmatched_tickers:
            return None
        
        # Check if market is closed (invalidate match)
        status = market.get("status", "").lower()
        if status == "closed":
            return None
        
        # Parse ticker
        parsed = parse_kalshi_ticker(ticker)
        if not parsed:
            self.unmatched_tickers.add(ticker)
            self.match_stats["unmatched"] += 1
            return None
        
        self.match_stats["total_attempted"] += 1
        
        # Get OddsAPI file path
        file_path = get_oddsapi_file_path(
            parsed["sport"],
            parsed["market_type"],
            parsed["date"],
            self.data_dir
        )
        
        if not file_path:
            self.unmatched_tickers.add(ticker)
            self.match_stats["unmatched"] += 1
            return None
        
        # Load OddsAPI data (use cache)
        file_path_str = str(file_path)
        if file_path_str not in self._df_cache:
            self._df_cache[file_path_str] = load_oddsapi_data(file_path)
        oddsapi_df = self._df_cache[file_path_str]
        
        if oddsapi_df.empty:
            self.unmatched_tickers.add(ticker)
            self.match_stats["unmatched"] += 1
            return None
        
        # Match based on market type
        match_key = None
        market_type = parsed["market_type"]
        
        if market_type == "GAME":
            match_key = match_h2h_market(ticker, parsed, oddsapi_df, parsed["sport"])
            if match_key:
                self.match_stats["h2h_matched"] += 1
        elif market_type == "SPREAD":
            match_key = match_spread_market(ticker, parsed, oddsapi_df, parsed["sport"])
            if match_key:
                self.match_stats["spread_matched"] += 1
        elif market_type == "TOTAL":
            match_key = match_total_market(ticker, parsed, oddsapi_df, parsed["sport"])
            if match_key:
                self.match_stats["total_matched"] += 1
        
        # Store match
        if match_key:
            self.matches[ticker] = match_key
            self.match_stats["matched"] += 1
            self._save_matches()
        else:
            self.unmatched_tickers.add(ticker)
            self.match_stats["unmatched"] += 1
        
        return match_key
    
    def get_oddsapi_rows(self, ticker: str, match_key: str, event_date: date) -> List[pd.Series]:
        """
        Get OddsAPI rows for a matched ticker.
        
        Args:
            ticker: Kalshi ticker
            match_key: Match key from find_match
            event_date: Event date
        
        Returns:
            List of matching OddsAPI rows
        """
        parsed = parse_kalshi_ticker(ticker)
        if not parsed:
            return []
        
        # Get file path
        file_path = get_oddsapi_file_path(
            parsed["sport"],
            parsed["market_type"],
            event_date,
            self.data_dir
        )
        
        if not file_path:
            return []
        
        # Load data
        oddsapi_df = load_oddsapi_data(file_path)
        if oddsapi_df.empty:
            return []
        
        # Extract match criteria from match_key
        # Use | as delimiter for all market types (to handle negative points and team names with dashes)
        match_parts = match_key.split("|")
        market_type = parsed["market_type"]
        
        matching_rows = []
        
        if market_type == "GAME":
            # Match key: "game_id|team" (using | to handle team names with dashes)
            if len(match_parts) >= 2:
                game_id = match_parts[0]
                team = match_parts[1]
                for _, row in oddsapi_df.iterrows():
                    if str(row.get("game_id", "")) == game_id and str(row.get("team", "")) == team:
                        matching_rows.append(row)
        elif market_type == "SPREAD":
            # Match key: "game_id|team|point" (using | to avoid -- with negative points)
            if len(match_parts) >= 3:
                game_id = match_parts[0]
                team = match_parts[1]
                point = float(match_parts[2])
                for _, row in oddsapi_df.iterrows():
                    if (str(row.get("game_id", "")) == game_id and
                        str(row.get("team", "")) == team and
                        abs(float(row.get("point", 0)) - point) < 0.01):
                        matching_rows.append(row)
        elif market_type == "TOTAL":
            # Match key: "game_id|home_team|point|team" (using | to avoid -- with negative points)
            if len(match_parts) >= 4:
                game_id = match_parts[0]
                home_team = match_parts[1]
                point = float(match_parts[2])
                team = match_parts[3]
                for _, row in oddsapi_df.iterrows():
                    if (str(row.get("game_id", "")) == game_id and
                        str(row.get("home_team", "")) == home_team and
                        abs(float(row.get("point", 0)) - point) < 0.01 and
                        str(row.get("team", "")) == team):
                        matching_rows.append(row)
        
        return matching_rows
    
    def get_weighted_price(self, ticker: str, match_key: str, event_date: date) -> Optional[float]:
        """
        Get weighted average price for a matched ticker.
        
        Args:
            ticker: Kalshi ticker
            match_key: Match key from find_match
            event_date: Event date
        
        Returns:
            Weighted average price, or None if no match
        """
        rows = self.get_oddsapi_rows(ticker, match_key, event_date)
        if not rows:
            return None
        
        return compute_weighted_average(rows, settings.ODDS_API_BOOKMAKER_WEIGHTS)
    
    def invalidate_match(self, ticker: str):
        """Invalidate a match (e.g., when market closes)."""
        if ticker in self.matches:
            del self.matches[ticker]
            self._save_matches()
    
    def get_stats(self) -> Dict[str, Any]:
        """Get matching statistics."""
        return {
            **self.match_stats,
            "cache_size": len(self.matches),
            "unmatched_count": len(self.unmatched_tickers),
        }
