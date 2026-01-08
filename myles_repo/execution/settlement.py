from app import state
from config import settings
from kalshi.markets import get_kalshi_markets
from kalshi.fees import kalshi_fee_per_contract
from bot_logging.csv_logger import log_exit_row
from positions.io import save_positions


def realize_if_settled():
    global positions

    keep = []
    for p in state.positions:
        try:
            mkts = get_kalshi_markets(p["event_ticker"], force_live=True)
            if not mkts:
                keep.append(p)
                continue
            m = next((x for x in mkts if x.get("ticker") == p["market_ticker"]), None)
            if not m:
                keep.append(p)
                continue

            status = (m.get("status") or "").lower()
            if status not in ("settled", "closed", "resolved"):
                keep.append(p)
                continue

            result = (m.get("result") or m.get("resolution") or "").lower()

            entry = p.get("effective_entry", p["entry_price"])
            fee_entry = kalshi_fee_per_contract(entry, is_maker=False)

            if result.startswith("yes"):
                pnl_ct = (1.0 - entry) - fee_entry
                state.wins += 1
                exit_px = 1.0
            elif result.startswith("no"):
                pnl_ct = -(entry + fee_entry)
                state.losses += 1
                exit_px = 0.0
            else:
                keep.append(p)
                continue

            cash = p["stake"] * pnl_ct
            state.realized_pnl += cash
            log_exit_row(p, exit_price=exit_px, pnl_cash=cash, settled=True)

        except Exception as e:
            print(f"⚠️ realize_if_settled error on {p.get('market_ticker')}: {e}")
            keep.append(p)

    state.positions[:] = keep
    save_positions()
