from typing import Optional
from config import settings
from odds_feed.formatting import _parse_period_clock


def _should_block_trading_by_game_time(
    period_clock: Optional[str],
    match_name: Optional[str],
    event_ticker: Optional[str] = None,
) -> Optional[bool]:
    if not period_clock or not match_name:
        return None

    parsed = _parse_period_clock(period_clock)
    if not parsed:
        return None

    period, minutes_remaining = parsed

    is_nba = event_ticker and str(event_ticker).startswith("KXNBAGAME-")

    if is_nba:
        return period == 4 and minutes_remaining <= settings.ODDS_FEED_EXIT_TIME_MINUTES

    is_womens = "(W)" in str(match_name)

    if is_womens:
        return period == 4 and minutes_remaining <= 8.0
    return period == 2 and minutes_remaining <= 8.0


def _should_block_early_game_trading(
    period_clock: Optional[str],
    match_name: Optional[str],
    event_ticker: Optional[str] = None,
) -> Optional[bool]:
    if not period_clock or not match_name:
        return None

    parsed = _parse_period_clock(period_clock)
    if not parsed:
        return None

    period, minutes_remaining = parsed

    if period == 1:
        is_nba = event_ticker and str(event_ticker).startswith("KXNBAGAME-")

        if is_nba:
            return minutes_remaining > 7.0

        is_womens = "(W)" in str(match_name)

        if is_womens:
            return minutes_remaining > 5.0
        return minutes_remaining > 15.0

    return False
