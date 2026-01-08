import math
from typing import Optional, Tuple
from config import settings
from odds_feed.formatting import _parse_period_clock
from kalshi.fees import kalshi_fee_per_contract


def devig_proportional(p_raw):
    s = sum(p_raw)
    return [x / s for x in p_raw] if s else [None] * len(p_raw)


def devig_shin_two_way(dec_home, dec_away, tol=1e-9, max_iter=100):
    ph = 1.0 / dec_home
    pa = 1.0 / dec_away
    s = ph + pa
    qh, qa = ph / s, pa / s
    z = 0.0

    def fair_q(q, z_):
        return (math.sqrt(z_ * z_ + 4 * (1 - z_) * q) - z_) / (2 * (1 - z_) + 1e-12)

    for _ in range(max_iter):
        fh, fa = fair_q(qh, z), fair_q(qa, z)
        f_val = (fh + fa) - 1.0
        if abs(f_val) < tol:
            break
        dz = 1e-5
        f_prime = (fair_q(qh, z + dz) + fair_q(qa, z + dz)) - 1.0
        d_f = (f_prime - f_val) / dz if abs(f_prime - f_val) > 1e-15 else 0.0
        if abs(d_f) < 1e-12:
            break
        z = max(0.0, min(0.999999, z - f_val / d_f))
    return fh, fa


def ev_settlement_yes(p: float, entry_price: float, yes_ask_raw: Optional[int] = None, yes_ask: Optional[float] = None):
    if entry_price is None:
        return None, None
    entry_fee = kalshi_fee_per_contract(entry_price, is_maker=(yes_ask is not None and entry_price < yes_ask))
    ev_cash = p - entry_price - entry_fee
    ev_pct = (ev_cash / entry_price) if entry_price > 0 else None
    return ev_cash, ev_pct


def ev_exit_yes(odds_prob, entry_price, yes_bid, yes_ask):
    return ev_settlement_yes(odds_prob, entry_price)


def ev_mark_to_bid_yes(entry_price: float, yes_bid: Optional[float]):
    if yes_bid is None or entry_price is None:
        return None
    exit_fee = kalshi_fee_per_contract(yes_bid, is_maker=True)
    return (yes_bid - entry_price) - exit_fee


def kelly_yes_with_costs(p, price, rt_cost=0.0):
    p_eff = min(max(price + rt_cost, 1e-6), 1 - 1e-6)
    b = (1 / p_eff) - 1
    q = 1 - p
    f = (b * p - q) / b
    return max(0.0, f)


def ev_per_contract(win_prob: float, entry_price: float) -> float:
    gross = win_prob * (1 - entry_price) - (1 - win_prob) * entry_price
    fees = kalshi_fee_per_contract(entry_price, is_maker=False)
    return gross - fees


def calculate_ev_buy(true_probability: float, ask_price: float, is_maker: bool = False) -> float:
    if ask_price is None or true_probability is None:
        return None
    fee_per_contract = kalshi_fee_per_contract(ask_price, is_maker=is_maker)
    ev = true_probability - ask_price - fee_per_contract
    return ev


def calculate_fill_probability(limit_price: float, current_bid: float,
                               current_ask: float, side: str = "yes",
                               spread: Optional[float] = None,
                               period_clock: Optional[str] = None,
                               match_name: Optional[str] = None) -> float:
    if current_bid is None or current_ask is None:
        return 0.0

    if side.lower() == "yes":
        if limit_price >= current_ask:
            return 1.0

        spread_width = spread if spread is not None else (current_ask - current_bid)
        if spread_width <= 0:
            return 0.0

        distance_from_ask = current_ask - limit_price
        if distance_from_ask >= spread_width:
            return 0.0

        base_probability = 1.0 - (distance_from_ask / spread_width)
        probability = (base_probability ** settings.FILL_PROB_EXPONENT) * settings.FILL_PROB_PENALTY

        if spread_width > settings.FILL_PROB_WIDE_SPREAD_THRESHOLD:
            probability *= (1.0 - settings.FILL_PROB_WIDE_SPREAD_PENALTY)

        if period_clock and match_name:
            parsed = _parse_period_clock(period_clock)
            if parsed:
                period, minutes_remaining = parsed
                is_womens = "(W)" in str(match_name)

                near_end = False
                if is_womens:
                    near_end = (period == 4 and minutes_remaining <= settings.FILL_PROB_NEAR_END_THRESHOLD_MINUTES)
                else:
                    near_end = (period == 2 and minutes_remaining <= settings.FILL_PROB_NEAR_END_THRESHOLD_MINUTES)

                if near_end:
                    probability *= (1.0 - settings.FILL_PROB_NEAR_END_PENALTY)

        return max(0.0, min(1.0, probability))

    if limit_price <= current_bid:
        return 1.0

    spread_width = spread if spread is not None else (current_ask - current_bid)
    if spread_width <= 0:
        return 0.0

    distance_from_bid = limit_price - current_bid
    if distance_from_bid >= spread_width:
        return 0.0

    base_probability = 1.0 - (distance_from_bid / spread_width)
    probability = (base_probability ** settings.FILL_PROB_EXPONENT) * settings.FILL_PROB_PENALTY

    if spread_width > settings.FILL_PROB_WIDE_SPREAD_THRESHOLD:
        probability *= (1.0 - settings.FILL_PROB_WIDE_SPREAD_PENALTY)

    if period_clock and match_name:
        parsed = _parse_period_clock(period_clock)
        if parsed:
            period, minutes_remaining = parsed
            is_womens = "(W)" in str(match_name)
            near_end = (period == 4 if is_womens else period == 2) and minutes_remaining <= settings.FILL_PROB_NEAR_END_THRESHOLD_MINUTES
            if near_end:
                probability *= (1.0 - settings.FILL_PROB_NEAR_END_PENALTY)

    return max(0.0, min(1.0, probability))


def choose_maker_vs_taker(odds_prob: float, current_bid: float, current_ask: float,
                         quantity: int, mid_price: float = None,
                         spread: Optional[float] = None,
                         period_clock: Optional[str] = None,
                         match_name: Optional[str] = None) -> tuple:
    if current_bid is None or current_ask is None:
        ev_taker = calculate_ev_buy(odds_prob, current_ask or 0.5, is_maker=False)
        return False, ev_taker, 0.0, current_ask or 0.5

    if mid_price is None:
        mid_price = (current_bid + current_ask) / 2.0

    if spread is None:
        spread = current_ask - current_bid

    ev_taker = calculate_ev_buy(odds_prob, current_ask, is_maker=False)
    ev_maker = calculate_ev_buy(odds_prob, mid_price, is_maker=True)

    fill_prob_maker = calculate_fill_probability(
        mid_price, current_bid, current_ask, side="yes",
        spread=spread, period_clock=period_clock, match_name=match_name
    )

    expected_ev_maker = (ev_maker or 0.0) * fill_prob_maker
    expected_ev_taker = ev_taker or 0.0

    if quantity > 50 and fill_prob_maker < 0.40:
        expected_ev_maker *= 0.8

    if fill_prob_maker < 0.20:
        best_ev = ev_taker if ev_taker is not None else 0.0
        return False, best_ev, fill_prob_maker, current_ask

    if fill_prob_maker > 0.60 and expected_ev_maker > expected_ev_taker * 0.9:
        return True, expected_ev_maker, fill_prob_maker, mid_price

    use_maker = expected_ev_maker > expected_ev_taker
    best_ev = max(expected_ev_maker, expected_ev_taker)
    order_price = mid_price if use_maker else current_ask

    return use_maker, best_ev, fill_prob_maker, order_price
