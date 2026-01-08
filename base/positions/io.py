"""
Position I/O utilities for saving and loading positions.
"""

import json
import os
from pathlib import Path
from typing import List, Dict, Any, Optional
from config import settings
from app import state


def resolve_positions_file() -> Path:
    """Resolve the positions file path."""
    return settings.POSITIONS_FILE


def save_positions():
    """Save current positions to file."""
    positions_file = resolve_positions_file()
    try:
        with open(positions_file, "w") as f:
            json.dump(state.positions, f, indent=2)
    except Exception as e:
        print(f"⚠️ Failed to save positions: {e}")


def load_positions() -> List[Dict[str, Any]]:
    """Load positions from file."""
    positions_file = resolve_positions_file()
    if not positions_file.exists():
        return []
    try:
        with open(positions_file, "r") as f:
            positions = json.load(f)
            if isinstance(positions, list):
                state.positions = positions
                return positions
            return []
    except Exception as e:
        print(f"⚠️ Failed to load positions: {e}")
        return []