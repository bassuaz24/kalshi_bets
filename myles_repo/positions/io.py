import json
import os
from config import settings
from app import state


def save_positions():
    for p in state.positions:
        for key in ("stake", "q_low", "q_high"):
            if key in p and isinstance(p[key], (int, float)):
                p[key] = int(round(p[key]))
    with open(settings.POSITIONS_FILE, "w") as f:
        json.dump(state.positions, f, indent=2)


def resolve_positions_file():
    env_path = os.getenv("KALSHI_POSITIONS_FILE")
    if env_path:
        return os.path.abspath(env_path)
    return os.path.join(settings.BASE_DIR, "positions.json")


def resolve_first_detection_times_file():
    positions_dir = os.path.dirname(settings.POSITIONS_FILE)
    return os.path.join(positions_dir, "first_detection_times.json")


def load_positions():
    if not os.path.exists(settings.POSITIONS_FILE):
        return []
    try:
        with open(settings.POSITIONS_FILE, "r") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
            if isinstance(data, dict) and "positions" in data:
                return data["positions"]
            print("⚠️ Unrecognized JSON format in positions file.")
            return []
    except Exception as e:
        print(f"⚠️ Error loading positions file: {e}")
        return []
