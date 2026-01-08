from typing import Dict, Any


def _is_ncaa_event(event: Dict[str, Any]) -> bool:
    league = (event.get("league") or {}).get("name") or ""
    country = (event.get("league") or {}).get("cc") or ""
    league_lower = league.lower()

    if "ncaa" in league_lower:
        return True
    if "college" in league_lower and "usa" in league_lower:
        return True
    if country == "USA" and any(
        key in league_lower
        for key in ["college", "ncaa", "u19"]
    ):
        return True
    return False


def _is_nba_event(event: Dict[str, Any]) -> bool:
    league = (event.get("league") or {}).get("name") or ""
    country = (event.get("league") or {}).get("cc") or ""
    league_lower = league.lower()

    if "nba" in league_lower:
        return True

    tournament = event.get("tournament") or {}
    tournament_name = (tournament.get("name") or "").lower()
    if "nba" in tournament_name:
        return True

    if "nba" in league_lower and country == "USA":
        return True

    return False
