#!/bin/bash
# Helper script to run Kalshi collector in the background
# Usage: ./kalshi_collector_service.sh [--date YYYY-MM-DD] [--output-dir PATH]

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# Change to base directory
cd "$BASE_DIR" || exit 1

# Activate virtual environment if it exists
if [ -d ".venv" ]; then
    source .venv/bin/activate
elif [ -d "../.venv" ]; then
    source ../.venv/bin/activate
fi

# Run the collector with all arguments passed through
exec python3 -m data_collection.kalshi_collector "$@"
