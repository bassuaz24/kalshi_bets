#!/bin/bash
# Helper script to manage the Kalshi collector as a launchd service
# Usage: ./manage_collector.sh {start|stop|restart|status|logs}

PLIST_NAME="com.kalshi.collector"
PLIST_FILE="$HOME/Library/LaunchAgents/${PLIST_NAME}.plist"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_PLIST="$SCRIPT_DIR/${PLIST_NAME}.plist"

case "$1" in
    install)
        echo "📦 Installing Kalshi collector service..."
        
        # Copy plist to LaunchAgents
        if [ ! -f "$SOURCE_PLIST" ]; then
            echo "❌ Error: $SOURCE_PLIST not found!"
            exit 1
        fi
        
        cp "$SOURCE_PLIST" "$PLIST_FILE"
        echo "✅ Installed plist to $PLIST_FILE"
        echo "💡 Edit $PLIST_FILE to customize arguments (e.g., --date)"
        echo "💡 Then run: ./manage_collector.sh start"
        ;;
    
    start)
        echo "▶️  Starting Kalshi collector service..."
        if [ ! -f "$PLIST_FILE" ]; then
            echo "❌ Error: Service not installed. Run: ./manage_collector.sh install"
            exit 1
        fi
        launchctl load "$PLIST_FILE" 2>/dev/null || launchctl load -w "$PLIST_FILE"
        echo "✅ Service started"
        ;;
    
    stop)
        echo "⏹️  Stopping Kalshi collector service..."
        if [ ! -f "$PLIST_FILE" ]; then
            echo "❌ Error: Service not installed"
            exit 1
        fi
        launchctl unload "$PLIST_FILE" 2>/dev/null || launchctl unload -w "$PLIST_FILE"
        echo "✅ Service stopped"
        ;;
    
    restart)
        echo "🔄 Restarting Kalshi collector service..."
        $0 stop
        sleep 2
        $0 start
        ;;
    
    status)
        echo "📊 Kalshi collector service status:"
        if [ ! -f "$PLIST_FILE" ]; then
            echo "❌ Service not installed"
            exit 1
        fi
        launchctl list | grep "$PLIST_NAME" || echo "⚠️  Service not running"
        ;;
    
    logs)
        echo "📋 Recent logs:"
        echo "--- stdout ---"
        tail -n 50 "$SCRIPT_DIR/kalshi_collector.log" 2>/dev/null || echo "No log file found"
        echo ""
        echo "--- stderr ---"
        tail -n 50 "$SCRIPT_DIR/kalshi_collector.error.log" 2>/dev/null || echo "No error log found"
        ;;
    
    uninstall)
        echo "🗑️  Uninstalling Kalshi collector service..."
        $0 stop 2>/dev/null
        if [ -f "$PLIST_FILE" ]; then
            rm "$PLIST_FILE"
            echo "✅ Service uninstalled"
        else
            echo "⚠️  Service not installed"
        fi
        ;;
    
    *)
        echo "Usage: $0 {install|start|stop|restart|status|logs|uninstall}"
        echo ""
        echo "Commands:"
        echo "  install   - Install the service (copy plist to LaunchAgents)"
        echo "  start     - Start the collector service"
        echo "  stop      - Stop the collector service"
        echo "  restart   - Restart the collector service"
        echo "  status    - Check if service is running"
        echo "  logs      - Show recent log output"
        echo "  uninstall - Remove the service"
        exit 1
        ;;
esac
