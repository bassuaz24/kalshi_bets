import os
import requests
import pandas as pd
from datetime import datetime
import pytz

UTC = pytz.utc
CST = pytz.timezone("America/Chicago") 

# === CONFIGURATION ===
API_KEY = os.getenv("ODDS_API_KEY") or "7c172c307d8c00a47ab126adc3d0a726"  # <-- Replace or use env var
REGION = "us"
MARKETS = "h2h,spreads,totals"
BOOKMAKERS = ["draftkings", "fanduel", "pinnacle"]

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

def convert_to_cst(iso_str: str) -> str:
    dt_utc = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    dt_cst = dt_utc.astimezone(CST)
    return dt_cst.strftime("%Y-%m-%d %H:%M:%S %Z")

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

def normalize_to_rows(sport_name: str, games: list) -> (list, set):
    rows = []
    market_keys = set()

    for game in games:
        # Skip non-EPL/UCL games if sport is Soccer
        if sport_name == "Soccer":
            league_name = game.get("league", "")
            if league_name not in SOCCER_LEAGUES:
                continue  

        game_time = game.get("commence_time")
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

                    rows.append({
                        "sport": sport_name,
                        "league": game.get("league", ""),
                        "game_id": game_id,
                        "start_time": convert_to_cst(game_time),
                        "bookmaker": book_name,
                        "market": market_type,
                        "team": outcome.get("name"),
                        "price": outcome.get("price"),
                        "point": outcome.get("point", None),
                        "home_team": home_team,
                        "away_team": away_team
                    })
    return rows, market_keys


def main():
    all_data = []

    for sport_name, sport_key in SPORT_KEYS.items():
        print(f"📡 Fetching odds for {sport_name} ({sport_key})...")
        data = fetch_odds(sport_key)
        if not data:
            continue

        rows, markets_found = normalize_to_rows(sport_name, data)

        if markets_found:
            print(f"🔎 Markets available for {sport_name}: {sorted(markets_found)}")

        if rows:
            df = pd.DataFrame(rows)
            df = df[CSV_COLUMNS]
            output_path = os.path.join(OUTPUT_DIR, f"{sport_name.lower()}_odds_{datetime.now().date()}.csv")
            df.to_csv(output_path, index=False)
            print(f"✅ Saved {len(df)} rows to {output_path}")
            all_data.extend(rows)
        else:
            print(f"⚠️ No odds data for {sport_name}")

    print(f"\n📊 Total odds rows collected: {len(all_data)}")



if __name__ == "__main__":
    if API_KEY == "your_api_key_here":
        print("❌ Please set your ODDS_API_KEY environment variable or edit the script.")
    else:
        main()
