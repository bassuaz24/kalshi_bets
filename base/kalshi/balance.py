"""
Kalshi balance and portfolio value utilities.
"""

import time
from config import settings
from app import state
from core.session import SESSION
from kalshi.auth import kalshi_headers


def get_kalshi_balance(force=False):
    """Get current Kalshi account balance."""
    if settings.PLACE_LIVE_KALSHI_ORDERS != "YES":
        if settings.VERBOSE:
            print(f"💵 SIM MODE BALANCE: ${settings.CAPITAL_SIM:.2f}")
        return settings.CAPITAL_SIM

    now = time.time()
    if not force and (now - state._last_balance_ts) < settings.BALANCE_CACHE_SECS:
        return state._last_balance_val

    path = "/trade-api/v2/portfolio/balance"
    headers = kalshi_headers("GET", path)
    try:
        res = SESSION.get(settings.KALSHI_BASE_URL + path, headers=headers, timeout=8)
        data = res.json()

        cash_val = None
        if "cash" in data:
            cash_val = float(data["cash"]) / 100.0
        elif "available_cash" in data:
            cash_val = float(data["available_cash"]) / 100.0
        elif "balances" in data and "available_cash" in data["balances"]:
            cash_val = float(data["balances"]["available_cash"]) / 100.0
        elif "balance" in data:
            cash_val = float(data["balance"]) / 100.0

        if cash_val is not None:
            if settings.VERBOSE:
                print(f"💰 LIVE KALSHI BALANCE: ${cash_val:,.2f}")
            state._last_balance_ts = now
            state._last_balance_val = cash_val
            return cash_val

        if settings.VERBOSE:
            print("⚠️ Unexpected Kalshi balance format:", data)
    except Exception as e:
        if settings.VERBOSE:
            print(f"❌ Kalshi balance fetch error: {e}")

    state._last_balance_ts = now
    state._last_balance_val = settings.CAPITAL_SIM
    return settings.CAPITAL_SIM


def get_kalshi_portfolio_value(force=False):
    """Get current Kalshi portfolio value (cash + positions)."""
    if settings.PLACE_LIVE_KALSHI_ORDERS != "YES":
        return None

    now = time.time()
    if not force and (now - state._last_portfolio_value_ts) < settings.BALANCE_CACHE_SECS and state._last_portfolio_value_val is not None:
        return state._last_portfolio_value_val

    path = "/trade-api/v2/portfolio/balance"
    headers = kalshi_headers("GET", path)
    try:
        res = SESSION.get(settings.KALSHI_BASE_URL + path, headers=headers, timeout=8)
        data = res.json()

        portfolio_val = None
        if "portfolio_value" in data:
            portfolio_val = float(data["portfolio_value"]) / 100.0
        elif "equity" in data:
            portfolio_val = float(data["equity"]) / 100.0
        elif "total_equity" in data:
            portfolio_val = float(data["total_equity"]) / 100.0

        if portfolio_val is not None:
            if settings.VERBOSE:
                print(f"💼 LIVE KALSHI PORTFOLIO VALUE: ${portfolio_val:,.2f}")
            state._last_portfolio_value_ts = now
            state._last_portfolio_value_val = portfolio_val
            return portfolio_val

        if settings.VERBOSE:
            print("⚠️ Unexpected Kalshi portfolio value format:", data)
    except Exception as e:
        if settings.VERBOSE:
            print(f"❌ Kalshi portfolio value fetch error: {e}")

    state._last_portfolio_value_ts = now
    return None