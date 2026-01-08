# Trading Bot Infrastructure

This is the reimplemented trading bot infrastructure based on `myles_repo` with several modifications.

## Structure

- `app/` - Main application loop and engine
- `config/` - Configuration and settings
- `core/` - Core utilities (time, session)
- `data_collection/` - Market data collection using OddsAPI
- `execution/` - Trade execution and position management
- `kalshi/` - Kalshi API integration
- `bot_logging/` - Logging and daily reporting
- `positions/` - Position tracking and PnL calculation
- `risk/` - Risk management (exposure, stop loss)
- `strategy/` - Strategy engine (to be implemented)
- `ui/` - Web-based UI for control and monitoring

## Setup

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Create a `.env` file in the base directory with:
```
KALSHI_API_KEY_ID=your_key_id
KALSHI_BASE_URL=https://trading-api.kalshi.com
KALSHI_PRIVATE_KEY_PATH=path/to/private_key.pem
ODDS_API_KEY=your_odds_api_key
PLACE_LIVE_KALSHI_ORDERS=NO  # Set to YES for live trading
CAPITAL_SIM=10000.0
```

3. Ensure you have a `private_key.pem` file for Kalshi authentication

## Running

### Start the algorithm with UI:
```bash
python -m ui.server
```
Then open http://localhost:8080 in your browser

### Start the algorithm only:
```bash
python -m app.main
```

### Collect data independently:
```python
from data_collection.oddsapi_client import collect_data_standalone
collect_data_standalone()
```

## Features

- **Data Collection**: Automatically collects market data from OddsAPI and Kalshi, separated by league (sports) or market (other)
- **Strategy Engine**: Placeholder for strategy implementation that will compute optimal entry points, bet sizes, stop loss, and take profit
- **Trade Execution**: Executes trades through Kalshi API with risk checks
- **Position Tracking**: Tracks all positions with realized/unrealized PnL
- **Risk Management**: Enforces exposure limits and monitors stop loss/take profit triggers
- **Logging**: Comprehensive logging of trades, orders, and metrics
- **Daily Reports**: Automatic daily reports of performance and orders
- **Web UI**: Web-based interface to start/stop algorithm and view performance

## Strategy Implementation

The strategy engine (`strategy/engine.py`) has a placeholder function `compute_optimal_trade()` that should be implemented with your actual strategy logic. It should:

1. Analyze market data to determine optimal entry points
2. Calculate optimal bet size
3. Compute dynamic stop loss and take profit values
4. Return trade parameters

The infrastructure will automatically:
- Check risk limits before executing
- Execute trades through Kalshi
- Monitor stop loss/take profit triggers and execute exits dynamically
- Track all positions and PnL