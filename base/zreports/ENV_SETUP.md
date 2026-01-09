# Environment Configuration Guide

## Quick Start

1. **Copy the template file:**
   ```bash
   cd base
   cp .env.example .env
   ```

2. **Edit `.env` with your actual values:**
   - Open `.env` in a text editor
   - Fill in your API keys and adjust settings as needed

3. **Verify your configuration:**
   ```bash
   python3 -c "from config import settings; print('✅ Configuration loaded successfully')"
   ```

## Required Configuration

### 1. Kalshi API Credentials

You **must** configure these to use the bot:

```bash
KALSHI_API_KEY_ID=your_api_key_id_here
PRIVATE_KEY_PATH=private_key.pem
```

**Where to get these:**
1. Log into [Kalshi Trade API](https://trade.kalshi.com/trade-api)
2. Generate an API key (you'll get a Key ID)
3. Download your private key file (`.pem` format)
4. Place the private key file in the `base/` directory (or update `PRIVATE_KEY_PATH`)

**Important:** Keep your private key file secure and never commit it to version control!

### 2. OddsAPI Key

You **must** configure this to collect market data:

```bash
ODDS_API_KEY=your_oddsapi_key_here
```

**Where to get this:**
1. Sign up at [The Odds API](https://the-odds-api.com/)
2. Get your free API key (500 requests/month free tier)
3. Copy the key into your `.env` file

### 3. Trading Mode

**CRITICAL:** Set this correctly to avoid placing real orders during testing!

```bash
# Simulation mode (recommended for testing)
PLACE_LIVE_KALSHI_ORDERS=NO

# Live trading mode (ONLY after thorough testing!)
PLACE_LIVE_KALSHI_ORDERS=YES
```

## Recommended Configuration

### For Development/Testing

```bash
# Simulation mode
PLACE_LIVE_KALSHI_ORDERS=NO
CAPITAL_SIM=10000.0

# Enable verbose logging
VERBOSE=True

# Standard timing (fast enough for testing)
STRATEGY_LOOP_INTERVAL=30.0
STOP_LOSS_CHECK_INTERVAL=2.0
UI_UPDATE_INTERVAL=1.0

# Enable WebSocket
WEBSOCKET_ENABLED=True
```

### For Production

```bash
# Live trading (ONLY after testing!)
PLACE_LIVE_KALSHI_ORDERS=YES

# Reduce verbose logging
VERBOSE=False

# Optimize timing for your strategy
STRATEGY_LOOP_INTERVAL=30.0
STOP_LOSS_CHECK_INTERVAL=2.0
UI_UPDATE_INTERVAL=1.0

# Enable WebSocket for real-time prices
WEBSOCKET_ENABLED=True

# Risk management (adjust based on your capital and risk tolerance)
MAX_TOTAL_EXPOSURE_PCT=0.30
MAX_EXPOSURE_PER_EVENT_PCT=0.10
MAX_STAKE_PCT=0.05
```

## Timing Configuration Explained

### Strategy Loop Interval (`STRATEGY_LOOP_INTERVAL`)
- **Default**: 30 seconds
- **What it does**: How often the main strategy runs (data collection + trade execution)
- **Recommendation**: 30s is good for most strategies. Decrease for more frequent checks, increase to reduce API calls.

### Stop Loss Check Interval (`STOP_LOSS_CHECK_INTERVAL`)
- **Default**: 2 seconds
- **What it does**: How often stop loss/take profit triggers are checked
- **Recommendation**: 2s is optimal - fast enough to catch price movements, not so fast it causes issues.

### UI Update Interval (`UI_UPDATE_INTERVAL`)
- **Default**: 1 second
- **What it does**: How often UI performance metrics are updated
- **Recommendation**: 1s provides smooth UI updates without excessive overhead.

### Reconciliation Interval (`RECONCILE_INTERVAL`)
- **Default**: 10 seconds
- **What it does**: How often local positions are synced with live Kalshi positions
- **Recommendation**: 10s is good for catching partial fills and settlement.

## WebSocket Configuration

### Enable/Disable WebSocket
```bash
WEBSOCKET_ENABLED=True  # Recommended: enables real-time price updates
# WEBSOCKET_ENABLED=False  # Falls back to REST API (slower, may hit rate limits)
```

### WebSocket URL
```bash
# Live environment (DO NOT CHANGE unless you know what you're doing)
KALSHI_WS_URL=wss://api.elections.kalshi.com/trade-api/ws/v2
```

### Reconnection Settings
```bash
# Initial delay before reconnecting (exponential backoff)
WEBSOCKET_RECONNECT_DELAY=5.0

# Maximum delay between reconnection attempts
WEBSOCKET_MAX_RECONNECT_DELAY=60.0

# How long before cached prices are considered stale (fallback to REST)
WEBSOCKET_PRICE_CACHE_STALE_SECS=60.0
```

## Risk Management Settings

### Maximum Stake Per Trade
```bash
MAX_STAKE_PCT=0.05  # 5% of capital per trade
```

### Maximum Total Exposure
```bash
MAX_TOTAL_EXPOSURE_PCT=0.30  # 30% of capital across all positions
```

### Maximum Exposure Per Event
```bash
MAX_EXPOSURE_PER_EVENT_PCT=0.10  # 10% of capital per event
```

**Recommendation:** Start conservative and adjust based on your risk tolerance and backtesting results.

## Logging Configuration

```bash
# Write detailed trade logs
WRITE_TRADES_CSV=True

# Generate daily performance reports
WRITE_DAILY_REPORTS=True

# Log session metrics
WRITE_SESSION_METRICS=True

# Verbose debug logging (useful for troubleshooting)
VERBOSE=False  # Set to True for detailed output
```

## UI Configuration

```bash
# Port for web UI
UI_PORT=8080

# Host address
# 127.0.0.1 = localhost only (more secure)
# 0.0.0.0 = accessible from network (use with caution)
UI_HOST=127.0.0.1
```

## Security Best Practices

1. **Never commit `.env` to version control**
   - Ensure `.env` is in your `.gitignore`
   - Use `.env.example` as a template (without secrets)

2. **Protect your private key**
   - Store `private_key.pem` securely
   - Set proper file permissions: `chmod 600 private_key.pem`
   - Never share or commit your private key

3. **Use simulation mode for testing**
   - Always test with `PLACE_LIVE_KALSHI_ORDERS=NO` first
   - Verify strategy logic before switching to live trading

4. **Limit UI access**
   - Use `UI_HOST=127.0.0.1` for localhost-only access
   - Only use `0.0.0.0` if you need network access and have proper security measures

## Verifying Your Configuration

After setting up your `.env` file, verify it works:

```bash
cd base

# Test configuration loading
python3 -c "from config import settings; print(f'API Key ID: {settings.API_KEY_ID[:10]}...'); print('✅ Config loaded successfully')"

# Test Kalshi authentication
python3 -c "from kalshi.auth import load_private_key; load_private_key(); print('✅ Private key loaded successfully')"

# Test WebSocket connection (if enabled)
python3 -c "from config import settings; print(f'WebSocket enabled: {settings.WEBSOCKET_ENABLED}'); print(f'WebSocket URL: {settings.KALSHI_WS_URL}')"
```

## Troubleshooting

### "ModuleNotFoundError: No module named 'dotenv'"
```bash
pip install python-dotenv
```

### "FileNotFoundError: private_key.pem"
- Ensure `PRIVATE_KEY_PATH` points to the correct location
- Use absolute path if needed: `PRIVATE_KEY_PATH=/full/path/to/private_key.pem`

### "Invalid API key" errors
- Verify your `KALSHI_API_KEY_ID` is correct
- Ensure your private key file matches the API key
- Check that you're using live credentials (not paper trading credentials)

### WebSocket connection failures
- Verify `WEBSOCKET_ENABLED=True`
- Check network connectivity
- Ensure firewall allows WebSocket connections (port 443)
- Try disabling WebSocket temporarily to use REST API fallback

## Example .env File

See `.env.example` for a complete template with all available options and comments.
