import os
import requests
import pandas as pd
from datetime import datetime, timedelta
import pytz

UTC = pytz.utc
CST = pytz.timezone("America/Chicago") 

# === CONFIGURATION ===
API_KEY = os.getenv("ODDS_API_KEY") or "c8596b4bd2b552cbf833c152ec3aade8"  # <-- Replace or use env var
REGION = "us"
MARKETS = "h2h,spreads,totals"
BOOKMAKERS = ["fanduel", "pinnacle", "betus", "betonlineag"]

SPORT_KEYS = {
    "NFL": "americanfootball_nfl",
    "CFB": "americanfootball_ncaaf",
    "NBA": "basketball_nba",
    "CBB": "basketball_ncaab",
    "Tennis": "tennis",
    "Soccer": "soccer"  
}

SOCCER_LEAGUES = {
    "English Premier League",
    "UEFA Champions League"
}

CSV_COLUMNS = [
    "sport",
    "league",
    "game_id",
    "start_time",
    "bookmaker",
    "market",
    "team",
    "price",
    "point",
    "home_team",
    "away_team"
]


OUTPUT_DIR = "oddsapi_outputs"
os.makedirs(OUTPUT_DIR, exist_ok=True)


def _as_cst_datetime(value) -> datetime:
    if value is None:
        raise ValueError("Missing datetime value")
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = UTC.localize(dt)
    return dt.astimezone(CST)


def convert_to_cst(value) -> str:
    return _as_cst_datetime(value).strftime("%Y-%m-%d %H:%M:%S %Z")


def _write_csv(df: pd.DataFrame, path: str):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="") as csvfile:
        df.to_csv(csvfile, index=False)

def fetch_odds(sport_key: str):
    url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds/"
    params = {
        "apiKey": API_KEY,
        "regions": REGION,
        "markets": MARKETS,
        "bookmakers": ",".join(BOOKMAKERS),
        "oddsFormat": "decimal",  # or "american"
        "dateFormat": "iso"
    }

    resp = requests.get(url, params=params)
    if resp.status_code != 200:
        print(f"❌ Error fetching {sport_key}: {resp.status_code} - {resp.text}")
        return None

    return resp.json()

def normalize_to_rows(sport_name: str, games: list, target_dates) -> (dict, set):
    rows_by_date = {}
    market_keys = set()

    for game in games:
        # Skip non-EPL/UCL games if sport is Soccer
        if sport_name == "Soccer":
            league_name = game.get("league", "")
            if league_name not in SOCCER_LEAGUES:
                continue  

        game_time = game.get("commence_time")
        if not game_time:
            continue
        try:
            game_time_cst = _as_cst_datetime(game_time)
        except Exception:
            continue
        game_date = game_time_cst.date()
        if game_date not in target_dates:
            continue

        home_team = game.get("home_team")
        away_team = game.get("away_team")
        game_id = game.get("id")

        for bookmaker in game.get("bookmakers", []):
            book_name = bookmaker["title"]
            for market in bookmaker.get("markets", []):
                market_type = market.get("key")
                market_keys.add(market_type)

                for outcome in market.get("outcomes", []):
                    price = outcome.get("price")
                    if price is None:
                        continue

                    rows_by_date.setdefault(game_date, []).append({
                        "sport": sport_name,
                        "league": game.get("league", ""),
                        "game_id": game_id,
                        "start_time": convert_to_cst(game_time_cst),
                        "bookmaker": book_name,
                        "market": market_type,
                        "team": outcome.get("name"),
                        "price": outcome.get("price"),
                        "point": outcome.get("point", None),
                        "home_team": home_team,
                        "away_team": away_team
                    })
    return rows_by_date, market_keys


def main():
    all_data = []
    today_cst = datetime.now(CST)
    today_date = today_cst.date()
    tomorrow_date = (today_cst + timedelta(days=1)).date()
    date_folder = today_cst.strftime("%Y-%m-%d")
    target_dates = {today_date, tomorrow_date}
    dated_output_dir = os.path.join(OUTPUT_DIR, date_folder)
    os.makedirs(dated_output_dir, exist_ok=True)

    for sport_name, sport_key in SPORT_KEYS.items():
        print(f"📡 Fetching odds for {sport_name} ({sport_key})...")
        data = fetch_odds(sport_key)
        if not data:
            continue

        rows_by_date, markets_found = normalize_to_rows(sport_name, data, target_dates)

        if markets_found:
            print(f"🔎 Markets available for {sport_name}: {sorted(markets_found)}")

        daily_written = 0
        for game_date in sorted(rows_by_date.keys()):
            rows = rows_by_date[game_date]
            if not rows:
                continue
            df = pd.DataFrame(rows)[CSV_COLUMNS]
            suffix = "2" if game_date == tomorrow_date else ""
            filename = f"{sport_name.lower()}{suffix}_odds.csv"
            output_path = os.path.join(dated_output_dir, filename)
            _write_csv(df, output_path)
            print(f"✅ Saved {len(df)} rows to {output_path}")
            all_data.extend(rows)
            daily_written += len(rows)

        if daily_written == 0:
            print(f"⚠️ No odds data for {sport_name}")

    print(f"\n📊 Total odds rows collected: {len(all_data)}")



if __name__ == "__main__":
    if API_KEY == "your_api_key_here":
        print("❌ Please set your ODDS_API_KEY environment variable or edit the script.")
    else:
        main()
