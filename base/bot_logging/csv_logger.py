"""
CSV logging for trades, orders, and metrics.
"""

import csv
import os
from pathlib import Path
from typing import Dict, Any
from config import settings
from app import state
from core.time import now_utc
from positions.metrics import get_position_summary


TRADES_LOG_FILE = Path(settings.BASE_DIR) / "trades_log.csv"
ORDERS_LOG_FILE = Path(settings.BASE_DIR) / "orders_log.csv"
METRICS_LOG_FILE = Path(settings.BASE_DIR) / "metrics_log.csv"


def log_trade(trade: Dict[str, Any]):
    """Log a trade to CSV file."""
    if not settings.WRITE_TRADES_CSV:
        return
    
    if not trade.get("stake") or trade["stake"] <= 0:
        return
    
    try:
        write_header = not TRADES_LOG_FILE.exists() or TRADES_LOG_FILE.stat().st_size == 0
        
        fieldnames = [
            "timestamp", "match", "market_ticker", "event_ticker",
            "side", "entry_price", "exit_price", "stake",
            "pnl", "entry_time", "exit_time", "exit_reason"
        ]
        
        with open(TRADES_LOG_FILE, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            if write_header:
                writer.writeheader()
            
            row = {
                "timestamp": now_utc().isoformat(),
                "match": trade.get("match", ""),
                "market_ticker": trade.get("market_ticker", ""),
                "event_ticker": trade.get("event_ticker", ""),
                "side": trade.get("side", ""),
                "entry_price": trade.get("entry_price", 0.0),
                "exit_price": trade.get("exit_price", 0.0),
                "stake": trade.get("stake", 0),
                "pnl": trade.get("pnl", 0.0),
                "entry_time": trade.get("entry_time", ""),
                "exit_time": trade.get("exit_time", ""),
                "exit_reason": trade.get("exit_reason", ""),
            }
            
            writer.writerow(row)
    except Exception as e:
        print(f"⚠️ Failed to log trade: {e}")


def log_order(order: Dict[str, Any]):
    """Log an order to CSV file."""
    if not settings.WRITE_TRADES_CSV:
        return
    
    try:
        write_header = not ORDERS_LOG_FILE.exists() or ORDERS_LOG_FILE.stat().st_size == 0
        
        fieldnames = [
            "timestamp", "market_ticker", "side", "action",
            "price", "quantity", "order_type", "status", "order_id"
        ]
        
        with open(ORDERS_LOG_FILE, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            if write_header:
                writer.writeheader()
            
            row = {
                "timestamp": now_utc().isoformat(),
                "market_ticker": order.get("market_ticker", ""),
                "side": order.get("side", ""),
                "action": order.get("action", ""),
                "price": order.get("price", 0.0),
                "quantity": order.get("quantity", 0),
                "order_type": order.get("order_type", ""),
                "status": order.get("status", ""),
                "order_id": order.get("order_id", ""),
            }
            
            writer.writerow(row)
    except Exception as e:
        print(f"⚠️ Failed to log order: {e}")


def log_metrics():
    """Log current metrics to CSV file."""
    if not settings.WRITE_SESSION_METRICS:
        return
    
    try:
        write_header = not METRICS_LOG_FILE.exists() or METRICS_LOG_FILE.stat().st_size == 0
        
        summary = get_position_summary()
        
        fieldnames = [
            "timestamp", "total_positions", "realized_pnl", "unrealized_pnl",
            "total_pnl", "equity", "total_exposure", "wins", "losses",
            "orders_placed", "orders_filled", "orders_cancelled"
        ]
        
        with open(METRICS_LOG_FILE, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            if write_header:
                writer.writeheader()
            
            row = {
                "timestamp": now_utc().isoformat(),
                "total_positions": summary.get("total_positions", 0),
                "realized_pnl": summary.get("realized_pnl", 0.0),
                "unrealized_pnl": summary.get("unrealized_pnl", 0.0),
                "total_pnl": summary.get("total_pnl", 0.0),
                "equity": summary.get("equity", 0.0),
                "total_exposure": summary.get("total_exposure", 0.0),
                "wins": summary.get("wins", 0),
                "losses": summary.get("losses", 0),
                "orders_placed": state.METRICS.get("orders_placed", 0),
                "orders_filled": state.METRICS.get("orders_filled", 0),
                "orders_cancelled": state.METRICS.get("orders_cancelled", 0),
            }
            
            writer.writerow(row)
    except Exception as e:
        print(f"⚠️ Failed to log metrics: {e}")