import time
from typing import Any, Dict, Iterable, Optional, List
from config import settings
from core.session import SESSION
from odds_feed.formatting import _format_epoch
from odds_feed.filters import _is_ncaa_event, _is_nba_event


def _betsapi_request(path: str, params: Dict[str, Any]) -> Dict[str, Any]:
    token = settings.API_BET_API
    if not token:
        print("⚠️ API_BET_API missing from .env - returning empty result")
        return {}

    url = f"{settings.BETSAPI_BASE}{path}"
    payload_params = dict(params)
    payload_params["token"] = token
    payload_params["_t"] = int(time.time() * 1000)

    last_err = None
    for attempt in range(1, settings.ODDS_FEED_MAX_RETRIES + 1):
        try:
            headers = {
                "Accept": "application/json",
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0",
            }
            resp = SESSION.get(url, params=payload_params, headers=headers, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("success") == 1:
                    return data
                last_err = data.get("error") or "BetsAPI error"
            else:
                last_err = f"HTTP {resp.status_code}: {resp.text[:200]}"
        except Exception as exc:
            last_err = str(exc)
        time.sleep(settings.ODDS_FEED_RETRY_SLEEP)
    raise RuntimeError(last_err or "BetsAPI request failed")


def fetch_event_moneyline(
    event_id: str, moneyline_key: str = settings.BASKETBALL_MONEYLINE_KEY
) -> Optional[Dict[str, Any]]:
    payload = _betsapi_request(settings.BETSAPI_EVENT_ODDS_PATH, {"event_id": event_id})
    odds = payload.get("results", {}).get("odds") or {}
    entries = odds.get(moneyline_key) or []
    for record in entries:
        home_od = record.get("home_od")
        away_od = record.get("away_od")
        if home_od in (None, "-", "") or away_od in (None, "-", ""):
            continue
        return {
            "home_odds": home_od,
            "away_odds": away_od,
            "score_snapshot": record.get("ss"),
            "period_clock": record.get("time_str"),
            "recorded_at": _format_epoch(record.get("add_time")) if record.get("add_time") else None,
        }
    return None


def _fetch_odds_feed_live_events(statuses: Optional[Iterable[str]] = None) -> list:
    events: List[Dict[str, Any]] = []
    page = 1

    while True:
        payload = _betsapi_request(
            settings.BETSAPI_EVENTS_INPLAY_PATH,
            {"sport_id": settings.BASKETBALL_SPORT_ID, "page": page},
        )
        results = payload.get("results") or []

        for evt in results:
            if _is_ncaa_event(evt) or _is_nba_event(evt):
                events.append(evt)

        pager = payload.get("pager") or {}
        total = pager.get("total")
        per_page = pager.get("per_page") or len(results)

        if not results:
            break
        if not pager or total is None or per_page is None:
            page += 1
            continue
        if page * per_page >= total:
            break
        page += 1

    return events
