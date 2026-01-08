"""
API Server for Kalshi Trading Bot

This module provides a read-only REST API to expose live game state and positions
from the EC2 trading bot. The API is consumed by an iPhone app that relays data
to an Apple Watch for real-time monitoring.

Design:
- Read-only API (no trade execution)
- Thread-safe access to trading bot state
- Lightweight JSON responses optimized for mobile/watch
- Low update frequency (seconds, not milliseconds)
"""

import threading
import time
from typing import List, Dict, Any, Optional
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
import uvicorn

# Import state accessor functions from main module
# These functions provide thread-safe read-only access to trading bot state
try:
    from api.state_access import (
        get_active_matches_for_api,
        get_positions_for_api,
        get_game_ticks_for_api,
    )
except ImportError:
    def get_active_matches_for_api():
        return []

    def get_positions_for_api():
        return []

    def get_game_ticks_for_api(game_id: str, limit: int = 20):
        return []

app = FastAPI(
    title="Kalshi Trading Bot API",
    description="Read-only API for live game state and positions",
    version="1.0.0"
)


def _extract_game_id(match: Dict[str, Any]) -> str:
    """Extract a unique game identifier from a match dict."""
    # Try event_ticker first, then ticker, then match name
    event_ticker = match.get("ticker", "")
    if event_ticker:
        return event_ticker
    match_name = match.get("match", "")
    if match_name:
        return match_name.replace(" ", "_").replace("(", "").replace(")", "")
    return "unknown"


def _format_sportsbook_odds(odds_feed: Dict[str, Any]) -> Optional[float]:
    """Extract sportsbook odds from odds_feed dict."""
    if not odds_feed:
        return None
    # Return home odds as a representative value (could be enhanced to return both)
    home_odds = odds_feed.get("home_odds")
    if home_odds:
        return float(home_odds)
    return None


def _get_kalshi_price(match: Dict[str, Any]) -> Optional[float]:
    """Extract Kalshi YES side price from match kalshi markets."""
    kalshi_markets = match.get("kalshi", [])
    if not kalshi_markets:
        return None
    
    # Find the first active market with a YES bid/ask
    for market in kalshi_markets:
        yes_bid = market.get("yes_bid")
        yes_ask = market.get("yes_ask")
        
        # Try to get mid price
        if yes_bid is not None and yes_ask is not None:
            # Convert from cents to decimal if needed
            try:
                bid = float(yes_bid) / 100.0 if yes_bid > 1 else float(yes_bid)
                ask = float(yes_ask) / 100.0 if yes_ask > 1 else float(yes_ask)
                return (bid + ask) / 2.0
            except (ValueError, TypeError):
                pass
        
        # Fallback to bid or ask
        if yes_bid is not None:
            try:
                return float(yes_bid) / 100.0 if yes_bid > 1 else float(yes_bid)
            except (ValueError, TypeError):
                pass
        
        if yes_ask is not None:
            try:
                return float(yes_ask) / 100.0 if yes_ask > 1 else float(yes_ask)
            except (ValueError, TypeError):
                pass
    
    return None


@app.get("/games/live")
def get_live_games():
    """
    Returns the latest snapshot for all active games.
    
    Each game includes:
    - game_id: Unique game identifier
    - score: Current score
    - time_remaining: Game clock
    - kalshi_price: Kalshi market price (YES side)
    - sportsbook_odds: Sportsbook odds
    - last_update: Timestamp of last update
    """
    try:
        matches = get_active_matches_for_api()
        result = []
        
        for match in matches:
            odds_feed = match.get("odds_feed", {})
            score_snapshot = odds_feed.get("score_snapshot", "")
            period_clock = odds_feed.get("period_clock", "")
            
            # Get last update timestamp
            last_update_ts = odds_feed.get("last_update_ts")
            if not last_update_ts:
                last_update_ts = time.time()
            
            game_data = {
                "game_id": _extract_game_id(match),
                "score": score_snapshot if score_snapshot else "N/A",
                "time_remaining": period_clock if period_clock else "N/A",
                "kalshi_price": _get_kalshi_price(match),
                "sportsbook_odds": _format_sportsbook_odds(odds_feed),
                "last_update": int(last_update_ts),
            }
            
            result.append(game_data)
        
        return JSONResponse(content={"games": result})
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching live games: {str(e)}")


@app.get("/positions")
def get_positions():
    """
    Returns current open positions.
    
    Each position includes:
    - market: Market identifier (market_ticker)
    - qty: Quantity (stake)
    - avg_price: Average entry price
    - unrealized_pnl: Unrealized profit/loss
    """
    try:
        positions = get_positions_for_api()
        result = []
        
        for pos in positions:
            position_data = {
                "market": pos.get("market_ticker", "unknown"),
                "qty": pos.get("stake", 0),
                "avg_price": pos.get("entry_price", 0.0),
                "unrealized_pnl": pos.get("unrealized_pnl"),
            }
            result.append(position_data)
        
        return JSONResponse(content={"positions": result})
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching positions: {str(e)}")


@app.get("/games/{game_id}/ticks")
def get_game_ticks(game_id: str, limit: int = 20):
    """
    Returns recent price updates for a specific game.
    
    Args:
        game_id: Game identifier
        limit: Maximum number of ticks to return (default 20)
    
    Returns:
        List of price tick dictionaries with timestamp and price
    """
    try:
        ticks = get_game_ticks_for_api(game_id, limit=limit)
        return JSONResponse(content={"game_id": game_id, "ticks": ticks})
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching game ticks: {str(e)}")


@app.get("/health")
def health_check():
    """Health check endpoint."""
    return JSONResponse(content={"status": "ok", "timestamp": int(time.time())})


def start_api_server(port: int = 8000, host: str = "0.0.0.0"):
    """
    Start the FastAPI server in a separate thread.
    
    Args:
        port: Port to run the server on (default 8000)
        host: Host to bind to (default 0.0.0.0 for all interfaces)
    
    Returns:
        Thread object running the server
    """
    def run_server():
        config = uvicorn.Config(
            app,
            host=host,
            port=port,
            log_level="info",
            access_log=False,  # Disable access logs for cleaner output
        )
        server = uvicorn.Server(config)
        server.run()
    
    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()
    print(f"🌐 API server started on http://{host}:{port}")
    print(f"   Endpoints: /games/live, /positions, /games/{{game_id}}/ticks, /health")
    return thread


if __name__ == "__main__":
    # For testing: run server directly
    start_api_server(port=8000)
    # Keep main thread alive
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n🛑 API server stopped")
