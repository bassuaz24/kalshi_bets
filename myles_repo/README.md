# Kalshi AWS Trading Bot

## High-Level Architecture: EC2 → iPhone → Apple Watch

### Purpose

This EC2 process is the **single source of truth** for live sports + Kalshi market data and positions.
Its responsibility is to **compute state**, not to manage UI, devices, or Apple-specific logic.

The Apple Watch app is **read-only**, used for real-time monitoring (similar to ESPN / Bloomberg watch apps).

## Responsibilities of the EC2 Process

### 1. Compute Live State

The EC2 code:

* Subscribes to or polls:
  * Live sports scores
  * Game clock
  * Kalshi market prices
  * Sportsbook odds
* Tracks:
  * Current positions
  * Average entry price
  * Unrealized PnL
* Updates this state continuously as events occur

This logic already exists in the trading engine.

### 2. Publish State via a Lightweight API

Instead of relying on `print()` logs, the EC2 process **exposes its current state as structured JSON**.

This allows external clients (iPhone / Watch) to read the latest snapshot at any time.

**Key principle:**

> EC2 pushes *state*, not UI instructions.

## API Contract (Read-Only)

### `/games/live`

Returns the latest snapshot for all active games.

Each game includes:

* Unique game identifier
* Current score
* Time remaining
* Kalshi market price (YES side)
* Sportsbook odds
* Timestamp of last update

Example:

```json
{
  "game_id": "DUKE_UVA",
  "score": "71–68",
  "time_remaining": "2:31",
  "kalshi_price": 0.63,
  "sportsbook_odds": -145,
  "last_update": 1735322212
}
```

### `/positions`

Returns current open positions.

Each position includes:

* Market identifier
* Quantity
* Average entry price
* Unrealized PnL

Example:

```json
{
  "market": "DUKE_UVA_YES",
  "qty": 42,
  "avg_price": 0.58,
  "unrealized_pnl": 18.40
}
```

### `/games/{game_id}/ticks` (optional)

Returns recent price updates for charting / ticker display.

* Small window (e.g., last 10–20 points)
* Optimized for low bandwidth

## Data Flow (Why It's Designed This Way)

```
EC2 (compute + state)
        ↓
iPhone app (networking + caching)
        ↓
Apple Watch (display only)
```

### Why EC2 does NOT talk directly to the watch

* Apple Watch has strict background limits
* iPhone handles authentication, retries, and batching
* Watch receives already-processed snapshots

This mirrors how professional trading terminals work.

## Design Constraints (Intentional)

* **Read-only API**
* **No trade execution**
* **No user credentials exposed**
* **Low update frequency tolerated (seconds, not milliseconds)**

The system is optimized for:

* Reliability
* Battery efficiency
* Situational awareness

Not for execution speed.

## Summary (One-Paragraph Version)

> This EC2 service computes live sports and Kalshi market state and exposes the current snapshot via a lightweight, read-only JSON API. The API is consumed by an iPhone app, which acts as a relay and cache for an Apple Watch monitoring interface. The watch displays real-time scores, prices, and positions for glanceable decision awareness, similar to ESPN or Bloomberg watch applications. All computation and trading logic remains centralized on EC2.

## Strategy Overview (What It Does)

At a high level the bot:
* Ingests live odds + score data, and maps games to Kalshi markets.
* Computes EV/Kelly-based entry sizing with fees and spread-aware adjustments.
* Manages risk with stop-loss, hedging bands, and profit-protection logic.
* Tracks positions locally and reconciles with live Kalshi positions when available.
* Publishes a read-only API snapshot for external clients (iPhone/Watch).

## Runtime Switches (High-Level)

Key switches live in `config/settings.py`:
* `PLACE_LIVE_KALSHI_ORDERS` — enable/disable live trading (simulation vs live).
* `ENABLE_NBA_TRADING` — toggle NBA trading on/off.
* `HEDGING_ENABLED` — enable/disable hedging logic.
* `PROFIT_PROTECTION_ENABLED` — enable/disable profit protection exits.
* `TRAILING_STOP_ENABLED` — enable/disable trailing stop exits.
* `ODDS_FEED_AGGRESSIVE_EXIT_ENABLED` — enable/disable odds-feed exits.
* `VERBOSE`, `PRINT_MARKET_TABLE` — control logging verbosity.
* CSV logging switches: `WRITE_SNAPSHOTS`, `WRITE_EVALS`, `WRITE_BOT_LOG`, etc.

Note: `config/settings.py` may be redacted for sharing. See `RUN_REQUIREMENTS.md` for required secrets and files.

## Running the API Server

The API server is automatically started when running the main trading bot. By default, it runs on port 8000. You can configure the port via the `API_PORT` environment variable:

```bash
export API_PORT=8080
python app/engine.py
```

The API will be available at `http://localhost:8000` (or your configured port).

### Testing the API

```bash
# Get all live games
curl http://localhost:8000/games/live

# Get all positions
curl http://localhost:8000/positions

# Get price ticks for a specific game
curl http://localhost:8000/games/KXNCAAMBGAME-25NOV29CBUORST/ticks
```

## Requirements

See `RUN_REQUIREMENTS.md` for:
* Required `.env` keys
* Email setup (optional)
* Kalshi private key location
* Runtime-generated files

## Dependencies

* `fastapi` - Web framework for the API
* `uvicorn[standard]` - ASGI server for FastAPI

Install with:
```bash
pip install fastapi uvicorn[standard]
```
