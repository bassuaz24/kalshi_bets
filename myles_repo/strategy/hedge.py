import math
from typing import Optional
from config import settings
from app import state
from kalshi.fees import kalshi_fee_per_contract
from kalshi.markets import market_yes_mid, get_kalshi_markets
from utils.tickers import event_key


def hedge_qty_bounds_target_roi(qA: float, pA: float, pB: float, r: float = settings.MIN_HEDGE_RETURN,
                                 yes_ask_A: Optional[float] = None, yes_ask_B: Optional[float] = None):
    is_maker_A = (yes_ask_A is not None and pA < yes_ask_A) if yes_ask_A is not None else False
    is_maker_B = (yes_ask_B is not None and pB < yes_ask_B) if yes_ask_B is not None else False
    fA = kalshi_fee_per_contract(pA, is_maker=is_maker_A)
    fB = kalshi_fee_per_contract(pB, is_maker=is_maker_B)

    denom_low = (1.0 - pB - fB - r * pB)
    denom_high = (pB * (1.0 + r) + fB)

    if denom_low <= 1e-9 or denom_high <= 1e-9:
        return None, None

    q_low = (qA * (pA * (1.0 + r) + fA)) / denom_low
    q_high = (qA * (1.0 - pA - fA - r * pA)) / denom_high

    if not (math.isfinite(q_low) and math.isfinite(q_high)):
        return None, None

    return max(0.0, q_low), max(0.0, q_high)


def hedge_outcome_rois(qA: float, pA: float, qB: float, pB: float,
                       yes_ask_A: Optional[float] = None, yes_ask_B: Optional[float] = None):
    is_maker_A = (yes_ask_A is not None and pA < yes_ask_A) if yes_ask_A is not None else False
    is_maker_B = (yes_ask_B is not None and pB < yes_ask_B) if yes_ask_B is not None else False
    fA = kalshi_fee_per_contract(pA, is_maker=is_maker_A)
    fB = kalshi_fee_per_contract(pB, is_maker=is_maker_B)
    L = max(1e-9, qA * pA + qB * pB)

    pnl_A = qA * (1 - pA - fA) - qB * (pB + fB)
    pnl_B = qB * (1 - pB - fB) - qA * (pA + fA)

    return pnl_A / L, pnl_B / L


def report_event_hedge_bands(event_ticker: str, kalshi_markets=None, label: str = ""):
    evt_key = event_key(event_ticker)
    event_positions = [
        p for p in state.positions
        if event_key(p.get("event_ticker")) == evt_key
        and p.get("side") == "yes"
        and p.get("stake", 0) > 0
    ]
    if not event_positions:
        return

    mkts = kalshi_markets or get_kalshi_markets(event_ticker, force_live=True) or []
    if not mkts:
        print("⚠️ Hedge-band snapshot skipped — no Kalshi markets available.")
        return

    yes_markets = [
        m for m in mkts
        if (m.get("market_type") == "binary") and (m.get("yes_sub_title") not in (None, ""))
    ]
    if len(yes_markets) > 2:
        print("⚠️ Hedge-band snapshot ambiguous — more than two YES markets detected.")
        return

    print(f"📐 Hedge bands @ live mids for {label or event_ticker}:")
    for pos in event_positions:
        opp_market = next(
            (
                m for m in mkts
                if event_key(m.get("event_ticker") or event_ticker) == evt_key
                and m.get("ticker") != pos.get("market_ticker")
            ),
            None,
        )
        if not opp_market:
            print(f"   - {pos['market_ticker']}: opposite market not found.")
            continue

        opp_mid = market_yes_mid(opp_market)
        if opp_mid is None:
            print(f"   - {pos['market_ticker']}: no mid available for {opp_market.get('ticker')}.")
            continue

        opp_mid = max(0.01, min(0.99, float(opp_mid)))

        q_low, q_high = hedge_qty_bounds_target_roi(
            float(pos["stake"]),
            float(pos["entry_price"]),
            float(opp_mid),
            r=settings.MIN_HEDGE_RETURN,
        )
        if q_low is None or q_high is None:
            continue
        ql_i = math.ceil(q_low)
        qh_i = math.floor(q_high)
        print(
            f"   - {pos['market_ticker']} hedge via {opp_market.get('ticker')} @ {opp_mid:.2%} → "
            f"q in [{ql_i}, {qh_i}]"
        )


def log_hedge_band_preview(existing_position: dict, candidate_market: dict, match_label: str):
    if not settings.SHOW_HEDGE_BAND_PREVIEW:
        return
    if not existing_position or not candidate_market:
        return

    hedge_mid = market_yes_mid(candidate_market)
    opp_label = candidate_market.get("yes_sub_title") or candidate_market.get("ticker") or "opposite side"

    if hedge_mid is None:
        return

    hedge_mid = max(0.01, min(0.99, float(hedge_mid)))

    try:
        q_band = hedge_qty_bounds_target_roi(
            float(existing_position.get("stake", 0)),
            float(existing_position.get("entry_price", 0)),
            float(hedge_mid),
            r=settings.MIN_HEDGE_RETURN,
        )
    except Exception:
        return

    if not q_band:
        return

    q_low, q_high = q_band
    if q_low is None or q_high is None:
        return
    try:
        ql_i = math.ceil(float(q_low))
        qh_i = math.floor(float(q_high))
    except (TypeError, ValueError):
        return

    print(
        f"   📐 Hedge preview: {opp_label} @ {hedge_mid:.2%} → q_low={ql_i}, q_high={qh_i}"
    )
