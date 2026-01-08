import re
import unicodedata
from data.team_maps import TEAM_MAP
from data.nba_abbrev import NBA_ABBREV_EXPANSION


def normalize_name(name: str) -> str:
    """Lowercase, strip punctuation/accents, return last name only for safer matching."""
    if not name:
        return ""
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("utf-8")
    parts = re.sub(r"[^a-zA-Z ]", "", name).strip().lower().split()
    return parts[-1] if parts else name.lower()


def kalshi_key3(name: str) -> list:
    """Return a list of possible 3-letter Kalshi keys for a player's name."""
    if not name:
        return []

    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("utf-8")
    tokens = re.sub(r"[^A-Za-z ]", " ", name).strip().split()
    if not tokens:
        return []

    candidates = []
    if len(tokens) >= 1:
        candidates.append(tokens[-1][:3].upper())
    if len(tokens) >= 2:
        candidates.append(tokens[-2][:3].upper())
        candidates.append(tokens[0][:3].upper())
    seen = set()
    return [x for x in candidates if not (x in seen or seen.add(x))]


def expand_nba_abbreviations(text: str) -> str:
    """Expand NBA team abbreviations in text to full names for better matching."""
    if not text:
        return text

    text_lower = text.lower().strip()
    sorted_expansions = sorted(NBA_ABBREV_EXPANSION.items(), key=lambda x: len(x[0]), reverse=True)

    for abbrev, full_name in sorted_expansions:
        pattern = r"\b" + re.escape(abbrev) + r"\b"

        if re.search(pattern, text_lower, re.IGNORECASE):
            def replace_func(match):
                matched = match.group(0)
                if matched.isupper():
                    return full_name.title()
                if matched[0].isupper():
                    return full_name.title()
                return full_name

            text = re.sub(pattern, replace_func, text, flags=re.IGNORECASE)
            text_lower = text.lower()

    return text


def normalize_tokens(s: str):
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


def smart_team_lookup(team_name: str, team_map: dict) -> tuple:
    """Intelligently match team name to team_map, handling mascot names."""
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
