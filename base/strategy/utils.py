"""
Utility functions for market discovery and team name matching.
"""

import re
import unicodedata
from typing import Tuple, Optional, Set
from data.team_maps import TEAM_MAP, NBA_TEAM_MAP
from config import settings


def normalize_team_name(name: str) -> str:
    """Normalize team name for matching (remove common prefixes/suffixes)."""
    if not name:
        return ""
    # Remove common suffixes
    name = re.sub(r'\s*\([^)]*\)\s*', '', name)  # Remove parentheticals
    name = re.sub(r'\s+', ' ', name).strip()  # Normalize whitespace
    return name.lower()


def normalize_tokens(s: str) -> Set[str]:
    """
    Normalize a team name string into comparable token(s).
    Converts known team names to canonical abbreviations (from team_map)
    and strips punctuation/nonletters for fuzzy matching.
    """
    if not s:
        return set()

    s = s.lower().strip()
    s = re.sub(r"\s*\([A-Z]{2}\)\s*", " ", s, flags=re.IGNORECASE)
    s = s.replace("&", " and ")
    s = s.replace("-", " ")
    s = re.sub(r"[()]", " ", s)
    s = re.sub(r"\bst\.\b", "st", s)
    s = re.sub(r"\bsaint\b", "st", s)
    s = re.sub(r"[^a-z\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()

    s_normalized = s
    for full_name in sorted(TEAM_MAP.keys(), key=len, reverse=True):
        normalized_key = full_name.lower().strip()
        normalized_key = normalized_key.replace("&", " and ")
        normalized_key = normalized_key.replace("-", " ")
        normalized_key = re.sub(r"[()]", " ", normalized_key)
        normalized_key = re.sub(r"\bst\.\b", "st", normalized_key)
        normalized_key = re.sub(r"\bsaint\b", "st", normalized_key)
        normalized_key = re.sub(r"[^a-z\s]", " ", normalized_key)
        normalized_key = re.sub(r"\s+", " ", normalized_key).strip()

        if not normalized_key:
            continue

        pattern = r"\b" + re.escape(normalized_key) + r"\b"
        if re.search(pattern, s_normalized):
            s_normalized = re.sub(pattern, TEAM_MAP[full_name].lower(), s_normalized)

    s_normalized = re.sub(r"\s+", " ", s_normalized).strip()
    return set(s_normalized.split()) if s_normalized else set()


def smart_team_lookup(team_name: str, team_map: dict) -> Tuple[Optional[str], str, Optional[str]]:
    """
    Intelligently match team name to team_map, handling mascot names.
    Returns: (team_code, confidence_level, matched_key)
    """
    if not team_name:
        return None, "fallback", None

    mascots = [
        "tigers", "bulldogs", "wildcats", "eagles", "bears", "panthers",
        "lions", "hawks", "falcons", "cougars", "huskies", "terriers",
        "cardinals", "blue devils", "tar heels", "spartans", "trojans",
        "aggies", "longhorns", "wolverines", "buckeyes", "crimson tide",
        "razorbacks", "gators", "seminoles", "hurricanes", "gamecocks",
        "orange", "hoyas", "jayhawks", "sooners", "cornhuskers",
        "volunteers", "crimson", "golden bears", "bruins", "sun devils",
        "rebels", "commodores", "vols", "knights",
        "mean green", "golden eagles", "red raiders", "mustangs",
        "rams", "golden gophers", "badgers", "fighting irish",
        "mountaineers", "cyclones", "horned frogs", "black bears",
        "great danes", "seahawks", "seawolves", "pirates", "raiders",
        "owls", "bison", "broncos", "redhawks", "retrievers", "colonials",
        "peacocks", "gaels", "stags", "zags", "friars", "explorers",
        "minutemen", "patriots", "river hawks", "pride",
        "jaspers", "terrapins", "blue jays", "purple eagles",
        "bombers", "ambassadors", "crusaders", "flying dutchmen",
        "green wave", "maroons", "mocs", "monarchs", "hatters",
        "billikens", "aces", "49ers", "rattlers", "aztecs",
        "bearcats", "beavers", "boilermakers", "buffalo", "chippewas",
        "cobbers", "ducks", "fighting camels", "golden flashes",
        "grizzlies", "hilltoppers", "hokies", "horned frogs",
        "jaguars", "lumberjacks", "midshipmen", "minutewomen",
        "musketeers", "nittany lions", "penguins", "phoenix",
        "quakers", "ramblers", "rebels", "redbirds", "running rebels",
        "salukis", "scarlet knights", "shockers", "sooners",
        "tar heels", "thundering herd", "utes", "vandals",
        "volunteers", "wolfpack", "yellow jackets",
    ]

    normalized = team_name.lower().strip()
    normalized = re.sub(r"\s*\([WMwm]\)\s*", " ", normalized)
    normalized = re.sub(r"\s*\([A-Z]{2}\)\s*", " ", normalized, flags=re.IGNORECASE)
    normalized = normalized.replace("&", " and ")
    normalized = normalized.replace("-", " ")
    normalized = re.sub(r"[()]", " ", normalized)
    normalized = re.sub(r"\bst\.\b", "st", normalized)
    normalized = re.sub(r"\bsaint\b", "st", normalized)
    normalized = normalized.replace("'", "")
    normalized = re.sub(r"[^a-z\s]", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()

    if normalized in team_map:
        return team_map[normalized], "exact", normalized

    words = normalized.split()
    if len(words) > 1:
        if words[-1] in mascots:
            without_mascot = " ".join(words[:-1])
            if without_mascot in team_map:
                return team_map[without_mascot], "without_mascot", without_mascot

        if len(words) > 2:
            last_two = f"{words[-2]} {words[-1]}"
            if last_two in mascots:
                without_mascot = " ".join(words[:-2])
                if without_mascot in team_map:
                    return team_map[without_mascot], "without_mascot", without_mascot

    for key in team_map:
        if len(key) >= 3:
            if re.match(rf"\b{re.escape(key)}\b", normalized):
                return team_map[key], "prefix_match", key

    return None, "fallback", None


def fuzzy_match_teams(name1: str, name2: str, threshold: float = 0.7) -> bool:
    """
    Simple fuzzy matching for team names.
    Returns True if names are similar enough (shared words or substring match).
    """
    n1 = normalize_team_name(name1)
    n2 = normalize_team_name(name2)
    
    if not n1 or not n2:
        return False
    
    # Exact match
    if n1 == n2:
        return True
    
    # Substring match (one name contains the other)
    # Reject when the longer name has a geographic/prefix modifier (e.g. "East", "West")
    # that indicates a different school - e.g. "East Texas A&M" vs "Texas A&M"
    _GEO_MODIFIERS = {"east", "west", "north", "south", "central"}
    if n1 in n2 or n2 in n1:
        shorter, longer = (n1, n2) if len(n1) <= len(n2) else (n2, n1)
        if shorter in longer:
            idx = longer.index(shorter)
            if idx > 0:
                prefix = longer[:idx].strip().lower()
                prefix_words = set(re.findall(r"[a-z0-9&]+", prefix))
                if prefix_words & _GEO_MODIFIERS:
                    # Different school (e.g. East Texas A&M vs Texas A&M) - reject
                    return False
        return True
    
    # Word overlap (at least threshold% of words match)
    tokens1 = normalize_tokens(n1)
    tokens2 = normalize_tokens(n2)
    
    if tokens1 and tokens2:
        overlap = len(tokens1 & tokens2)
        min_words = min(len(tokens1), len(tokens2))
        if min_words > 0 and overlap / min_words >= threshold:
            return True
    
    return False
