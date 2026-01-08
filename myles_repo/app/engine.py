"""Thin glue module for the basketball bot."""

import os
import sys

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from app.loop import main

__all__ = ["main"]


if __name__ == "__main__":
    main()
