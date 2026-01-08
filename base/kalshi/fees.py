"""
Kalshi fee calculations.
"""

import math


def kalshi_fee(num_contracts: int, price: float, is_maker: bool = False) -> float:
    """Calculate Kalshi fee for a trade."""
    fee_rate = 0.0175 if is_maker else 0.07
    raw = fee_rate * num_contracts * price * (1 - price)
    return math.ceil(raw * 100) / 100.0


def kalshi_fee_per_contract(price: float, is_maker: bool = False) -> float:
    """Calculate Kalshi fee per contract."""
    return kalshi_fee(1, price, is_maker=is_maker)