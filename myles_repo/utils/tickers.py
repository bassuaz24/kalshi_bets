import re
from datetime import datetime
from config import settings
from core.time import UTC
from data.team_maps import TEAM_MAP, NBA_TEAM_MAP
from utils.names import smart_team_lookup


def make_ncaa_event_ticker(home_team: str, away_team: str, event_date) -> list:
    """
    Build Kalshi NCAA Basketball event ticker from home/away team names and date.
    Returns men's or women's ticker format based on whether team names contain "(W)".
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


def make_nba_event_ticker(home_team: str, away_team: str, event_date) -> list:
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


def normalize_event_ticker(t):
    """Cleans and normalizes event tickers so comparisons match across sides."""
    if not t:
        return ""
    t = t.lower().strip()
    t = re.sub(r"-set\d+", "", t)
    t = re.sub(r"[_\s]+", "", t)
    return t


def event_key(evt: str) -> str:
    """Canonical event identifier used for comparisons and locks."""
    return normalize_event_ticker(evt or "")
