"""
Kalshi position management utilities.
"""

from typing import Optional, List, Dict, Any
from config import settings
from core.session import SESSION
from kalshi.auth import kalshi_headers


def get_live_positions() -> List[Dict[str, Any]]:
    """Fetch current live positions from Kalshi."""
    path = "/trade-api/v2/portfolio/positions"
    headers = kalshi_headers("GET", path)
    try:
        res = SESSION.get(settings.KALSHI_BASE_URL + path, headers=headers, timeout=8)
        txt = res.text[:300]
        if res.status_code != 200:
            print(f"⚠️ Positions fetch failed: {res.status_code} {txt}")
            if settings.VERBOSE:
                print(f"   Full response: {res.text[:500]}")
            return []

        try:
            data = res.json()
        except Exception:
            print(f"⚠️ Non-JSON /positions body: {txt}")
            return []

        live_positions = []

        def _push_pos(ticker, side, contracts, avg_price, event_ticker=None):
            try:
                if not ticker:
                    return
                side = (side or "").lower()
                qty = abs(int(contracts or 0))
                if qty <= 0:
                    return
                ap = float(avg_price or 0.0)
                if ap > 1.0:
                    ap = ap / 100.0
                live_positions.append({
                    "ticker": ticker,
                    "side": side,
                    "contracts": qty,
                    "avg_price": ap,
                    "event_ticker": event_ticker or "",
                })
            except Exception as e:
                print(f"⚠️ parse helper err: {e}")

        for mp in (data.get("market_positions") or []):
            ticker = mp.get("ticker")
            if not ticker:
                continue
            position = mp.get("position", 0)
            if position == 0:
                continue

            side = "yes" if position > 0 else "no"
            contracts = abs(position)

            avg_price = None
            market_exposure_dollars_str = mp.get("market_exposure_dollars", "0")
            try:
                market_exposure_dollars = float(market_exposure_dollars_str)
                if abs(position) > 0 and market_exposure_dollars > 0:
                    avg_price = market_exposure_dollars / abs(position)
                    if avg_price > 1.0:
                        avg_price = avg_price / 100.0
            except (ValueError, TypeError):
                pass

            if avg_price is None or avg_price <= 0:
                total_traded = mp.get("total_traded", 0)
                total_traded_dollars_str = mp.get("total_traded_dollars", "0")
                try:
                    total_traded_dollars = float(total_traded_dollars_str)
                    if total_traded > 0:
                        avg_price = total_traded_dollars / total_traded
                        if avg_price > 1.0:
                            avg_price = avg_price / 100.0
                except (ValueError, TypeError):
                    pass

            if avg_price is None or avg_price <= 0:
                if settings.VERBOSE:
                    print(f"⚠️ Could not calculate avg_price for {ticker}, skipping")
                continue

            evt = mp.get("event_ticker", "")
            _push_pos(ticker, side, contracts, avg_price, evt)

        for ep in (data.get("event_positions") or []):
            evt = ep.get("event_ticker") or ep.get("event") or ""
            nested_markets = ep.get("market_positions") or ep.get("markets") or []
            for mp in nested_markets:
                ticker = mp.get("ticker")
                if not ticker:
                    continue
                position = mp.get("position", 0)
                if position == 0:
                    continue

                side = "yes" if position > 0 else "no"
                contracts = abs(position)

                avg_price = None
                market_exposure_dollars_str = mp.get("market_exposure_dollars", "0")
                try:
                    market_exposure_dollars = float(market_exposure_dollars_str)
                    if abs(position) > 0 and market_exposure_dollars > 0:
                        avg_price = market_exposure_dollars / abs(position)
                        if avg_price > 1.0:
                            avg_price = avg_price / 100.0
                except (ValueError, TypeError):
                    pass

                if avg_price is None or avg_price <= 0:
                    total_traded = mp.get("total_traded", 0)
                    total_traded_dollars_str = mp.get("total_traded_dollars", "0")
                    try:
                        total_traded_dollars = float(total_traded_dollars_str)
                        if total_traded > 0:
                            avg_price = total_traded_dollars / total_traded
                            if avg_price > 1.0:
                                avg_price = avg_price / 100.0
                    except (ValueError, TypeError):
                        pass

                if avg_price is None or avg_price <= 0:
                    continue

                _push_pos(ticker, side, contracts, avg_price, evt)

        raw_positions = (
            data.get("positions")
            or data.get("portfolio", {}).get("positions")
            or data.get("orders")
            or []
        )
        for p in raw_positions:
            try:
                ticker = p.get("ticker") or p.get("market_ticker") or p.get("id")
                side = (p.get("side") or "").lower()
                contracts = int(
                    p.get("contracts_count") or p.get("count") or
                    p.get("size") or p.get("contracts") or 0
                )
                ap_raw = (p.get("average_price") or p.get("avg_price") or p.get("entry_price") or 0)
                avg_price = float(ap_raw) / (100.0 if float(ap_raw or 0) > 1 else 1)
                if contracts > 0 and ticker:
                    _push_pos(ticker, side, contracts, avg_price, p.get("event_ticker", ""))
            except Exception as e:
                print(f"⚠️ Error parsing legacy position: {e} → {p}")
                continue

        if settings.VERBOSE and live_positions:
            print(f"✅ Parsed {len(live_positions)} positions from Kalshi API")

        return live_positions

    except Exception as e:
        print(f"❌ Error fetching live positions: {e}")
        if settings.VERBOSE:
            import traceback
            traceback.print_exc()
        return []