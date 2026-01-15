# Running Kalshi Collector as a Background Service

This guide explains how to run the Kalshi collector in the background so it continues running even when your terminal is closed or your computer goes to sleep (when it wakes up).

## Option 1: macOS Launch Agent (Recommended)

This method uses macOS's `launchd` service manager to run the collector as a background service.

### Setup

1. **Install the service:**
   ```bash
   cd /Users/Brett/kdata/kalshi_bets/base/data_collection
   ./manage_collector.sh install
   ```

2. **Edit the plist file (optional):**
   - The plist file is installed to: `~/Library/LaunchAgents/com.kalshi.collector.plist`
   - Edit it to add `--date` argument if needed:
     ```xml
     <string>exec python3 -m data_collection.kalshi_collector --date 2026-01-12</string>
     ```

3. **Start the service:**
   ```bash
   ./manage_collector.sh start
   ```

### Managing the Service

```bash
# Start the collector
./manage_collector.sh start

# Stop the collector
./manage_collector.sh stop

# Restart the collector
./manage_collector.sh restart

# Check if it's running
./manage_collector.sh status

# View logs
./manage_collector.sh logs

# Uninstall the service
./manage_collector.sh uninstall
```

### Logs

- **Standard output:** `base/data_collection/kalshi_collector.log`
- **Errors:** `base/data_collection/kalshi_collector.error.log`

### Notes

- The service will **not** automatically start on boot (set `RunAtLoad` to `true` in the plist if you want that)
- The service will restart if it crashes (due to `KeepAlive`)
- The service will **not** run while your computer is sleeping, but will resume when it wakes up

---

## Option 2: Using nohup (Simple Alternative)

For a simpler approach that doesn't require system-level configuration:

```bash
cd /Users/Brett/kdata/kalshi_bets/base

# Activate virtual environment if needed
source .venv/bin/activate  # or ../.venv/bin/activate

# Run with nohup (runs in background, survives terminal close)
nohup python3 -m data_collection.kalshi_collector > data_collection/kalshi_collector.log 2>&1 &

# To run with a specific date:
nohup python3 -m data_collection.kalshi_collector --date 2026-01-12 > data_collection/kalshi_collector.log 2>&1 &
```

### Managing nohup Process

```bash
# Find the process
ps aux | grep kalshi_collector

# Kill the process (replace PID with actual process ID)
kill <PID>

# Or kill all python processes running the collector
pkill -f "data_collection.kalshi_collector"
```

### View Logs

```bash
# Follow the log in real-time
tail -f base/data_collection/kalshi_collector.log
```

---

## Option 3: Using screen or tmux

For an interactive session that persists:

### Using screen:
```bash
# Start a new screen session
screen -S kalshi_collector

# Run the collector
cd /Users/Brett/kdata/kalshi_bets/base
source .venv/bin/activate
python3 -m data_collection.kalshi_collector

# Detach: Press Ctrl+A, then D
# Reattach: screen -r kalshi_collector
```

### Using tmux:
```bash
# Start a new tmux session
tmux new -s kalshi_collector

# Run the collector
cd /Users/Brett/kdata/kalshi_bets/base
source .venv/bin/activate
python3 -m data_collection.kalshi_collector

# Detach: Press Ctrl+B, then D
# Reattach: tmux attach -t kalshi_collector
```

---

## Which Method Should I Use?

- **Launch Agent (Option 1)**: Best for long-term, set-and-forget operation. Survives reboots (if configured), user logout, and terminal closure.
- **nohup (Option 2)**: Simplest, good for one-off long-running tasks. Survives terminal closure but not logout or reboot.
- **screen/tmux (Option 3)**: Best if you want to interact with the process occasionally. Survives terminal closure but not logout or reboot.

---

## Troubleshooting

### Service won't start
- Check logs: `./manage_collector.sh logs`
- Verify Python path in plist matches your system: `which python3`
- Check if virtual environment path is correct

### Service stops unexpectedly
- Check error log: `cat base/data_collection/kalshi_collector.error.log`
- Verify `.env` file exists and has correct API keys
- Check network connectivity

### Can't find the process
- For launchd: `launchctl list | grep kalshi`
- For nohup: `ps aux | grep kalshi_collector`
- For screen: `screen -ls`
- For tmux: `tmux ls`
