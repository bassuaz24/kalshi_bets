import math


def kalshi_fee(num_contracts: int, price: float, is_maker: bool = False) -> float:
    fee_rate = 0.0175 if is_maker else 0.07
    raw = fee_rate * num_contracts * price * (1 - price)
    return math.ceil(raw * 100) / 100.0


def kalshi_fee_per_contract(price: float, is_maker: bool = False) -> float:
    return kalshi_fee(1, price, is_maker=is_maker)


def maker_entry_fee(entry_price: float, yes_ask_raw: int, yes_ask: float = None) -> float:
    if yes_ask is not None:
        is_maker = entry_price < yes_ask
    else:
        is_maker = False
    return kalshi_fee_per_contract(entry_price, is_maker=is_maker)
