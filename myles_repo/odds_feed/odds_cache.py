import json
from config import settings
from app import state


def _load_odds_snapshot() -> None:
    if state._odds_snapshot_loaded:
        return
    try:
        with open(settings.ODDS_FEED_DELTA_PATH, "r", encoding="utf-8") as fh:
            state._odds_prev_snapshot = json.load(fh)
            state._odds_snapshot_loaded = True
    except FileNotFoundError:
        state._odds_prev_snapshot = {}
        state._odds_snapshot_loaded = False
    except Exception as exc:
        print(f"⚠️ Failed to load odds snapshot cache: {exc}")
        state._odds_prev_snapshot = {}
        state._odds_snapshot_loaded = False


def _save_odds_snapshot(snapshot):
    state._odds_prev_snapshot = snapshot
    try:
        with open(settings.ODDS_FEED_DELTA_PATH, "w", encoding="utf-8") as fh:
            json.dump(snapshot, fh, indent=2, sort_keys=True)
        state._odds_snapshot_loaded = True
    except Exception as exc:
        print(f"⚠️ Failed to write odds snapshot cache: {exc}")
