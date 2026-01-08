"""Main entry point for the trading bot."""

import os
import sys
from pathlib import Path

# Add base directory to path
_BASE_ROOT = Path(__file__).parent.parent.absolute()
if str(_BASE_ROOT) not in sys.path:
    sys.path.insert(0, str(_BASE_ROOT))

from app.loop import main

__all__ = ["main"]


if __name__ == "__main__":
    main()