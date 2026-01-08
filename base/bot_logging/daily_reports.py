"""
Daily reporting system for orders and performance.
"""

import os
import pandas as pd
from pathlib import Path
from datetime import datetime, date
from typing import Dict, Any, List
from config import settings
from app import state
from positions.metrics import get_position_summary
from bot_logging.csv_logger import TRADES_LOG_FILE, ORDERS_LOG_FILE, METRICS_LOG_FILE


REPORTS_DIR = Path(settings.BASE_DIR) / "daily_reports"
REPORTS_DIR.mkdir(exist_ok=True)


def generate_daily_report(report_date: date = None) -> Dict[str, Any]:
    """Generate daily report for orders and performance.
    
    Args:
        report_date: Date to generate report for (default: today)
    
    Returns:
        Dictionary with report data
    """
    if report_date is None:
        report_date = date.today()
    
    report_file = REPORTS_DIR / f"report_{report_date.strftime('%Y-%m-%d')}.csv"
    
    # Load daily data from logs
    trades_data = []
    orders_data = []
    metrics_data = []
    
    if TRADES_LOG_FILE.exists():
        try:
            df_trades = pd.read_csv(TRADES_LOG_FILE)
            df_trades["timestamp"] = pd.to_datetime(df_trades["timestamp"])
            df_trades = df_trades[df_trades["timestamp"].dt.date == report_date]
            trades_data = df_trades.to_dict("records")
        except Exception as e:
            print(f"⚠️ Error loading trades data: {e}")
    
    if ORDERS_LOG_FILE.exists():
        try:
            df_orders = pd.read_csv(ORDERS_LOG_FILE)
            df_orders["timestamp"] = pd.to_datetime(df_orders["timestamp"])
            df_orders = df_orders[df_orders["timestamp"].dt.date == report_date]
            orders_data = df_orders.to_dict("records")
        except Exception as e:
            print(f"⚠️ Error loading orders data: {e}")
    
    if METRICS_LOG_FILE.exists():
        try:
            df_metrics = pd.read_csv(METRICS_LOG_FILE)
            df_metrics["timestamp"] = pd.to_datetime(df_metrics["timestamp"])
            df_metrics = df_metrics[df_metrics["timestamp"].dt.date == report_date]
            metrics_data = df_metrics.to_dict("records")
        except Exception as e:
            print(f"⚠️ Error loading metrics data: {e}")
    
    # Generate summary
    summary = get_position_summary()
    
    # Calculate daily statistics
    daily_pnl = sum(t.get("pnl", 0.0) for t in trades_data)
    daily_trades = len(trades_data)
    daily_orders = len(orders_data)
    
    # Create report
    report = {
        "date": report_date.isoformat(),
        "summary": summary,
        "daily_pnl": daily_pnl,
        "daily_trades": daily_trades,
        "daily_orders": daily_orders,
        "trades": trades_data,
        "orders": orders_data,
        "metrics": metrics_data,
    }
    
    # Save report to CSV
    try:
        report_df = pd.DataFrame([{
            "date": report_date.isoformat(),
            "daily_pnl": daily_pnl,
            "daily_trades": daily_trades,
            "daily_orders": daily_orders,
            "total_positions": summary.get("total_positions", 0),
            "realized_pnl": summary.get("realized_pnl", 0.0),
            "unrealized_pnl": summary.get("unrealized_pnl", 0.0),
            "total_pnl": summary.get("total_pnl", 0.0),
            "equity": summary.get("equity", 0.0),
            "wins": summary.get("wins", 0),
            "losses": summary.get("losses", 0),
        }])
        report_df.to_csv(report_file, index=False)
        print(f"📊 Daily report saved to {report_file}")
    except Exception as e:
        print(f"⚠️ Failed to save daily report: {e}")
    
    return report