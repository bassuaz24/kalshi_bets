import pandas as pd
import numpy as np
from datetime import datetime, timezone
import regex as re
import math
import time
from collections import defaultdict
import pytz
import shin
import os
import sys
import argparse
import uuid
import requests
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import csv

from scipy.stats import norm
from scipy.optimize import brentq
from scipy.special import expit, logit

from rapidfuzz.fuzz import ratio


# =========================
# ======  CONFIG  =========
# =========================

class CONFIG:
    HOST = "https://api.elections.kalshi.com"
    HOST_FALLBACKS = [
        "https://trading-api.kalshi.com",
        "https://api.kalshi.com",
        "https://trading.kalshi.com",
    ]
    API_KEY = os.environ.get("KALSHI_API_KEY")
    AUTH_MODE = "kalshi_pss"
    PRIVATE_KEY_PATH = os.environ.get("KALSHI_PRIVATE_KEY_PATH")

    DRY_RUN = True
    REQ_TIMEOUT = 15
    MAX_RETRIES = 3
    RETRY_SLEEP = 1.0

    MIN_TICK = 0.01
    MIN_PRICE = 0.01
    MAX_PRICE = 0.99

    LOG_DIR = "live_betting/orders_log"
    
    DATE = '2025-12-15'
    ODDS_SPORT = 'nba' #cbbm, cbbm2, cbbw2, cfb, cfb2, nba, nfl
    KALSHI_SPORT = 'nba' #ncaabm, ncaabw, ncaaf, nba, nfl
    ODDS_DIR = "data_collection/updated_scripts/oddsapi_outputs"
    KALSHI_DIR = "data_collection/updated_scripts/kalshi_data_logs"
    OUTPUT_DIR = "live_betting/analysis_outputs"

    WINNERS_EDGE = 0.01
    SPREAD_EDGE = 0.02
    TOTALS_EDGE = 0.01
    WINNERS_EV_THRESHOLD = 0.10
    SPREADS_EV_THRESHOLD = 0.10
    KELLY_UPPERBOUND = 1
    TOTAL_BANKROLL = 300.00
    WINNERS_PROPORTION = 0.75
    SPREADS_PROPORTION = 1 - WINNERS_PROPORTION
    Q1_WEIGHT = 1.00
    Q2_WEIGHT = 1.00
    Q3_WEIGHT = 1.00
    Q4_WEIGHT = 1.00

REPO_ROOT = Path(__file__).resolve().parent.parent

WINNERS_BANKROLL = CONFIG.WINNERS_PROPORTION * CONFIG.TOTAL_BANKROLL
SPREADS_BANKROLL = CONFIG.TOTAL_BANKROLL - CONFIG.WINNERS_PROPORTION

try:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
except ImportError:
    sys.exit("❌ Missing dependency 'cryptography'. Install with: pip install cryptography")


# =========================
# =====  UTILITIES  =======
# =========================


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now_utc().isoformat().replace("+00:00", "Z")


def to_f(x) -> Optional[float]:
    try:
        return float(x)
    except Exception:
        return None


def clamp_price(p: float) -> Optional[float]:
    if p is None or math.isnan(p):
        return None
    p = max(CONFIG.MIN_PRICE, min(CONFIG.MAX_PRICE, p))
    return round(round(p / CONFIG.MIN_TICK) * CONFIG.MIN_TICK, 4)


def _ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)


def log_csv(filename: str, fieldnames: List[str], row: Dict[str, Any]):
    _ensure_dir(CONFIG.LOG_DIR)
    path = f"{CONFIG.LOG_DIR}/{filename}"
    is_new = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        if is_new:
            w.writeheader()
        w.writerow(row)


def log_order(action: str, payload: Dict[str, Any], resp: Any):
    row = {
        "ts": now_iso(),
        "action": action,
        "ticker": payload.get("ticker"),
        "side": payload.get("side"),
        "type": payload.get("type"),
        "price": payload.get("price"),
        "count": payload.get("count"),
        "tif": payload.get("time_in_force"),
        "client_order_id": payload.get("client_order_id"),
        "result": json.dumps(resp)[:1800],
    }
    fields = ["ts", "action", "ticker", "side", "type", "price", "count", "tif", "client_order_id", "result"]
    log_csv(f"orders_log_{now_utc().strftime('%Y-%m-%d')}.csv", fields, row)


def log_health(msg: str, **kw):
    row = {"ts": now_iso(), "msg": msg}
    row.update(kw)
    fields = ["ts", "msg"] + sorted([k for k in row.keys() if k not in {"ts", "msg"}])
    log_csv("health_log.csv", fields, row)


# =========================
# ===== HTTP / CLIENT =====
# =========================


def _load_private_key(path: str):
    with open(path, "rb") as key_file:
        return serialization.load_pem_private_key(key_file.read(), password=None, backend=default_backend())


def _sign_pss_text(private_key, text: str) -> str:
    message = text.encode("utf-8")
    signature = private_key.sign(
        message,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256(),
    )
    import base64

    return base64.b64encode(signature).decode("utf-8")


def _headers(extra: Optional[Dict[str, str]] = None, method: Optional[str] = None, path: Optional[str] = None) -> Dict[str, str]:
    base = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "kalshi-auto-trader/1.0",
    }
    mode = (CONFIG.AUTH_MODE or "").lower()
    api_key_clean = (CONFIG.API_KEY or "").strip().replace("\n", "")

    if mode == "kalshi_pss":
        key_id = api_key_clean
        priv_path = os.getenv("KALSHI_PRIVATE_KEY_PATH") or CONFIG.PRIVATE_KEY_PATH
        if not key_id:
            sys.exit("❌ Set key id via KALSHI_API_KEY or --api-key.")
        if not priv_path:
            sys.exit("❌ Set private key path via KALSHI_PRIVATE_KEY_PATH or CONFIG.PRIVATE_KEY_PATH.")
        priv_path = os.path.expanduser(priv_path)
        if not os.path.exists(priv_path):
            sys.exit(f"❌ Private key file not found: {priv_path}")
        if not method or not path:
            sys.exit("❌ Internal error: method/path required for kalshi_pss signing.")
        ts = str(int(time.time() * 1000))
        path_clean = "/trade-api/v2/" + path.lstrip("/").split("?")[0]
        msg = ts + method.upper() + path_clean
        priv = _load_private_key(priv_path)
        sig = _sign_pss_text(priv, msg)
        base.update(
            {
                "KALSHI-ACCESS-KEY": key_id,
                "KALSHI-ACCESS-SIGNATURE": sig,
                "KALSHI-ACCESS-TIMESTAMP": ts,
            }
        )
    else:
        sys.exit("❌ Only kalshi_pss auth supported.")

    if extra:
        base.update(extra)
    return base


def http_request(method: str, path: str, params: Optional[dict] = None, body: Optional[dict] = None, extra_headers: Optional[dict] = None) -> Optional[dict]:
    url = f"{CONFIG.HOST.rstrip('/')}/trade-api/v2/{path.lstrip('/')}"
    data = json.dumps(body) if body is not None else None

    for attempt in range(1, CONFIG.MAX_RETRIES + 1):
        try:
            if CONFIG.DRY_RUN and method.upper() in {"POST", "DELETE"}:
                return {"dry_run": True, "echo": {"path": path, "params": params, "body": body}}
            r = requests.request(
                method.upper(),
                url,
                headers=_headers(extra_headers, method=method, path=path),
                params=params,
                data=data,
                timeout=CONFIG.REQ_TIMEOUT,
            )
            if r.status_code in (200, 201, 204):
                if r.status_code == 204 or not r.text.strip():
                    return {"ok": True}
                try:
                    return r.json()
                except Exception:
                    return json.loads(r.text)
            if r.status_code in {429, 500, 502, 503, 504}:
                time.sleep(CONFIG.RETRY_SLEEP * attempt)
                continue
            print(f"❌ {method} {url} {r.status_code}: {r.text[:300]}")
            return None
        except requests.RequestException as e:
            print(f"⚠️ {method} {url} error: {e}")
            time.sleep(CONFIG.RETRY_SLEEP * attempt)
    return None


def place_order(payload: Dict[str, Any], idem_key: str) -> Optional[dict]:
    return http_request("POST", "portfolio/orders", body=payload, extra_headers={"Idempotency-Key": idem_key})


def fetch_total_bankroll() -> Optional[float]:
    try:
        payload = http_request("GET", "portfolio/balance")
        if payload and isinstance(payload, dict):
            val = to_f(payload.get("balance") or payload.get("cash_balance") or payload.get("portfolio_value"))
            if val is not None and val > 0:
                return val
    except Exception as e:
        log_health("balance_fetch_error", error=str(e))
    return None

# =========================
# ======= ORDERS ==========
# =========================


def idem_key(ticker: str) -> str:
    bucket = int(time.time() // 5)
    base = f"{ticker}|{bucket}"
    import hashlib

    return hashlib.sha256(base.encode()).hexdigest()


def submit_order(row: pd.Series, direction: str):
    ticker = row.get("ticker")
    contracts = row.get("num_contracts")
    start_time = row.get("start_time")
    if ticker is None or contracts is None:
        return
    try:
        count = int(contracts)
    except Exception:
        return
    if count <= 0:
        return

    buy_no = direction in {"buy_no", "no"}
    sell_yes = direction == "sell_yes"
    side = "no" if buy_no else "yes"
    action = "sell" if sell_yes else "buy"
    price_field = "no_bid" if buy_no else "yes_bid"
    price = clamp_price(to_f(row.get(price_field)))
    if price is None:
        return

    payload = {
        "ticker": ticker,
        "side": side,
        "action": action,
        "type": "limit",
        "count": count,
        "client_order_id": str(uuid.uuid4()),
    }
    
    if start_time and pd.notna(start_time):
        payload['expiration_ts'] = int(start_time.timestamp())
    
    price_cents = int(round(price * 100))
    if buy_no:
        payload["no_price"] = price_cents
    else:
        payload["yes_price"] = price_cents

    resp = place_order(payload, idem_key(ticker))
    log_order("place", payload, resp)
    mode = "LIVE" if not CONFIG.DRY_RUN else "DRY-RUN"
    if resp and (resp.get("dry_run") or resp.get("ok") or resp.get("order")):
        print(f"🪙 {mode} order: {ticker} side={side} action={action} size={count} price={price:.2f}")
    else:
        print(f"❌ order failed for {ticker} side={side} size={count} price={price}")


def process_dataframe(df: pd.DataFrame, source: str):
    for _, row in df.iterrows():
        direction = row.get("buy_direction")
        if isinstance(direction, str) and direction.lower() in {"yes", "no"}:
            submit_order(row, f"buy_{direction.lower()}")
            continue



#compute avg fair probability
#betus good for nba, pinnacle, betonline best for everything, fanduel pretty good

odds_df = pd.read_csv(f"../data_collection/updated_scripts/oddsapi_outputs/{CONFIG.DATE}/{CONFIG.ODDS_SPORT}_odds.csv")
odds_df.drop(columns=['league'], inplace=True)
odds_df.rename(columns={'price': 'odds'}, inplace=True)

# =========================
# ======  Calculate Fair Prob  =========
# =========================

odds_df['vig_prob'] = 1 / odds_df['odds']

def remove_vig_probs_add(df):
    df = df.copy()
    df['fair_prb'] = np.nan

    grouped = df.groupby(['game_id', 'bookmaker', 'market'])

    for _, group in grouped:
        if len(group) < 2:
            continue
        probs = group['vig_prob']
        total = probs.sum()
        if total == 0:
            continue
        fair_probs = (probs / total).round(4)
        df.loc[group.index, 'fair_prb'] = fair_probs

    return df

def remove_vig_probs_shin(df):
    df = df.copy()
    df['fair_prb'] = np.nan

    grouped = df.groupby(['game_id', 'bookmaker', 'market'])

    for _, group in grouped:
        if len(group) < 2:
            continue
        odds = group['odds'].values
        fair_probs = shin.calculate_implied_probabilities(odds)
        df.loc[group.index, 'fair_prb'] = fair_probs

    return df

def devig_probit(p1, p2):
    """if p1 <= 0 or p2 <= 0 or p1 >= 1 or p2 >= 1:
        total = p1 + p2
        return p1 / total, p2 / total
    if p1 + p2 <= 1:
        total = p1 + p2
        return p1 / total, p2 / total"""
  
    z1 = norm.ppf(p1)
    z2 = norm.ppf(p2)
    f = lambda lam: norm.cdf(z1 - lam) + norm.cdf(z2 - lam) - 1
    lam = brentq(f, -15, 15)
    
    q1 = norm.cdf(z1 - lam)
    q2 = norm.cdf(z2 - lam)
    return q1, q2

def remove_vig_probs_probit(df):
    df = df.copy()
    df['fair_prb'] = np.nan
    grouped = df.groupby(['game_id', 'bookmaker', 'market'])
    for _, group in grouped:
        if len(group) != 2:
            continue
        p1, p2 = group['vig_prob'].values
        q1, q2 = devig_probit(p1, p2)
        df.loc[group.index, 'fair_prb'] = [q1, q2]
    return df

def devig_logit(p1, p2):
    z1 = logit(p1)
    z2 = logit(p2)
    # Solve for λ such that logistic(z1 - λ) + logistic(z2 - λ) = 1
    f = lambda lam: expit(z1 - lam) + expit(z2 - lam) - 1
    lam = brentq(f, -50, 50)
    q1 = expit(z1 - lam)
    q2 = expit(z2 - lam)
    return q1, q2

def remove_vig_probs_logit(df):
    df = df.copy()
    df['fair_prb'] = np.nan
    grouped = df.groupby(['game_id', 'bookmaker', 'market'])
    for _, group in grouped:
        if len(group) != 2:
            continue
        p1, p2 = group['vig_prob'].values
        q1, q2 = devig_logit(p1, p2)
        df.loc[group.index, 'fair_prb'] = [q1, q2]
    return df

odds_df = remove_vig_probs_logit(odds_df)


odds_winners_df = odds_df[odds_df['market'] == 'h2h'].copy()
odds_spreads_df = odds_df[odds_df['market'] == 'spreads'].copy()
odds_spreads_df = odds_spreads_df.loc[(odds_spreads_df['point'].notna()) & (odds_spreads_df['point'] < 0)]
odds_totals_df  = odds_df[odds_df['market'] == 'totals'].copy()

# Average per-team fair probabilities across DraftKings/FanDuel/Pinnacle for winners_df
WEIGHTS = {
    "Pinnacle": 0.3,
    "BetOnline.ag": 0.3,
    "BetUS": 0.2,
    "FanDuel": 0.2
}

def wavg(x, df):
    idx = x.index
    bookmakers = df.loc[idx, 'bookmaker']
    w = np.array([WEIGHTS[b] for b in bookmakers])
    return np.average(x.values, weights=w)

mask = odds_winners_df['fair_prb'].notna()
avg_by_team = (
    odds_winners_df.loc[mask]
    .groupby(['game_id', 'team'])['fair_prb']
    .transform(lambda x: wavg(x, odds_winners_df))
    .round(4)
)
odds_winners_df.loc[mask, 'avg_fair_prb'] = avg_by_team
odds_winners_df.loc[~mask, 'avg_fair_prb'] = pd.NA

#Average fair probabilities for spreads for same game, point spread, and team
mask = odds_spreads_df['fair_prb'].notna()
avg_by_point = (
    odds_spreads_df.loc[mask]
    .groupby(['game_id', 'point', 'team'])['fair_prb']
    .transform(lambda x: wavg(x, odds_spreads_df))
    .round(4)
)
odds_spreads_df['avg_fair_prb'] = avg_by_point

#Average fair probabilities for totals for same game, point spread, direction (Over/Under)
mask = odds_totals_df['fair_prb'].notna()
avg_by_tot_point = (
    odds_totals_df.loc[mask]
    .groupby(['game_id', 'point', 'team'])['fair_prb']
    .transform(lambda x: wavg(x, odds_totals_df))
    .round(4)
)
odds_totals_df['avg_fair_prb'] = avg_by_tot_point

# ===================================
# ======  Load Kalshi Data  =========
# ===================================

kalshi_winners_df = pd.read_csv(f"../data_collection/updated_scripts/kalshi_data_logs/{CONFIG.DATE}/{CONFIG.KALSHI_SPORT}_winners.csv")
if CONFIG.KALSHI_SPORT != 'ncaabw':
    kalshi_totals_df = pd.read_csv(f"../data_collection/updated_scripts/kalshi_data_logs/{CONFIG.DATE}/{CONFIG.KALSHI_SPORT}_totals.csv")
    kalshi_spreads_df = pd.read_csv(f"../data_collection/updated_scripts/kalshi_data_logs/{CONFIG.DATE}/{CONFIG.KALSHI_SPORT}_spreads.csv")

if (CONFIG.KALSHI_SPORT == 'ncaaf') | (CONFIG.KALSHI_SPORT == 'nfl'):
    kalshi_spreads_df['points'] = kalshi_spreads_df['title'].str.extract(r'over ([\d.]+) points\?').astype(float)
    kalshi_totals_df["points"] = kalshi_totals_df["ticker"].str.extract(r"-([0-9.]+)$").astype(float)
elif (CONFIG.KALSHI_SPORT == 'ncaab') | (CONFIG.KALSHI_SPORT == 'ncaabm') | (CONFIG.KALSHI_SPORT == 'ncaabw') | (CONFIG.KALSHI_SPORT == 'nba'):
    kalshi_spreads_df['points'] = kalshi_spreads_df['title'].str.extract(r'over ([\d.]+) Points\?').astype(float)
    kalshi_totals_df["points"] = kalshi_totals_df["ticker"].str.extract(r"-([0-9.]+)$").astype(float)

columns_to_drop = ['timestamp', 'market_type']
kalshi_winners_df.drop(columns=columns_to_drop, inplace=True)
if CONFIG.KALSHI_SPORT != 'ncaabw':
    kalshi_spreads_df.drop(columns=columns_to_drop, inplace=True)
    kalshi_totals_df.drop(columns=columns_to_drop, inplace=True)


#get names from kalshi_winners_df
def extract_teams_from_winners(title):
    title = title.replace(" Winner?", "")
    if " at " in title:
        right, left = title.split(" at ", 1)
    elif " vs " in title:
        right, left = title.split(" vs ", 1)
    else:
        return pd.Series([None, None])  
    left = re.sub(r'\bSt\.$', 'St', left.strip())
    right = re.sub(r'\bSt\.$', 'St', right.strip())
    return pd.Series([left, right])

kalshi_winners_df[['home_team', 'away_team']] = kalshi_winners_df['title'].apply(extract_teams_from_winners)
unique_rows = kalshi_winners_df.drop_duplicates(subset=['home_team', 'away_team'])
flat_teams = pd.unique(unique_rows[['home_team', 'away_team']].values.ravel())
kalshi_winners_teams = flat_teams.tolist()

#get names from kalshi_totals_df
def extract_teams_from_totals(title):
    title = title.replace(": Total Points", "")
    if " at " in title:
        right, left = title.split(" at ", 1)
        left = re.sub(r'\bSt\.$', 'St', left.strip())
        right = re.sub(r'\bSt\.$', 'St', right.strip())
        return pd.Series([left, right])
    return None

kalshi_totals_df[['home_team', 'away_team']] = kalshi_totals_df['title'].apply(extract_teams_from_totals)
unique_rows = kalshi_winners_df.drop_duplicates(subset=['home_team', 'away_team'])
flat_teams = pd.unique(unique_rows[['home_team', 'away_team']].values.ravel())
kalshi_totals_teams = flat_teams.tolist()

#get names from kalshi_spreads_df
def extract_team_from_spreads(title):
    if " wins by " in title:
        team = title.split(" wins by ", 1)[0].strip()
        team = re.sub(r'\bSt\.$', 'St', team)
        return team
    return None

kalshi_spreads_df['team'] = kalshi_spreads_df['title'].apply(extract_team_from_spreads)
unique_teams_spread = kalshi_spreads_df['team'].drop_duplicates()
kalshi_spreads_teams = unique_teams_spread.tolist()

# ===================================
# ======  Match Team Names  =========
# ===================================

def fuzzy_match_kalshi_to_odds(kalshi_teams, odds_team_names):
    matched_kalshi = []
    matched_odds = []
    candidates_dict = defaultdict(list)

    kalshi_sorted = sorted(kalshi_teams, key=lambda x: x[0] if x else '')
    remaining_odds = sorted(odds_team_names.tolist().copy())

    for kalshi_name in kalshi_sorted:
        candidates = []
        for odds_name in remaining_odds:
            if kalshi_name in odds_name:
                candidates.append(odds_name)
        if len(candidates) == 1:
            candidates_dict[candidates[0]].append(kalshi_name)
        elif len(candidates) > 1:
            best_fit = candidates[0]
            best_ratio = ratio(best_fit, kalshi_name)
            for name in candidates:
                curr_ratio = ratio(name, kalshi_name)
                if curr_ratio > best_ratio:
                    best_fit = name
                    best_ratio = curr_ratio
            candidates_dict[best_fit].append(kalshi_name)
    
    for odd, kalsh in candidates_dict.items():
        best_fit = kalsh[0]
        best_ratio = ratio(best_fit, odd)
        if len(kalsh) > 1:
            for name in kalsh:
                curr_ratio = ratio(name, odd)
                if curr_ratio > best_ratio:
                    best_fit = name
                    best_ratio = curr_ratio
        matched_odds.append(odd)
        matched_kalshi.append(best_fit)

    return matched_kalshi, matched_odds


# Winners / h2h
odds_teams_winners = odds_winners_df['team'].unique()
matched_kalshi_h2h, matched_odds_h2h = fuzzy_match_kalshi_to_odds(
    kalshi_winners_teams,
    odds_teams_winners
)

# Spreads
odds_teams_spreads = odds_spreads_df['team'].unique()
matched_kalshi_spreads, matched_odds_spreads = fuzzy_match_kalshi_to_odds(
    kalshi_spreads_teams,
    odds_teams_spreads
)

# Totals (match only Over/Under)
totals_odds_df = odds_df[odds_df['market'] == 'totals']
odds_totals_teams = pd.unique(totals_odds_df[['home_team', 'away_team']].values.ravel())
matched_kalshi_totals, matched_odds_totals = fuzzy_match_kalshi_to_odds(
    kalshi_totals_teams,
    odds_totals_teams
)

matched_names = {
    'h2h': {
        'kalshi': matched_kalshi_h2h,
        'odds': matched_odds_h2h
    },
    'spreads': {
        'kalshi': matched_kalshi_spreads,
        'odds': matched_odds_spreads
    },
    'totals': {
        'kalshi': matched_kalshi_totals,
        'odds': matched_odds_totals
    }
}


#Run this in main loop to verify matches
assert(len(matched_names['h2h']['kalshi']) == len(matched_names['h2h']['odds']))
assert(len(matched_names['spreads']['kalshi']) == len(matched_names['spreads']['odds']))
assert(len(matched_names['totals']['kalshi']) == len(matched_names['totals']['odds']))

# ==================================
# ======= Filter DataFrames ======== 
# ==================================
odds_winners_df = odds_winners_df[
    odds_winners_df['home_team'].isin(matched_names['h2h']['odds']) |
    odds_winners_df['away_team'].isin(matched_names['h2h']['odds'])
].drop_duplicates(subset='team').sort_values(by='home_team').reset_index(drop=True)

kalshi_winners_df = kalshi_winners_df[
    kalshi_winners_df['home_team'].isin(matched_names['h2h']['kalshi']) |
    kalshi_winners_df['away_team'].isin(matched_names['h2h']['kalshi'])
].sort_values(by='home_team').reset_index(drop=True)

odds_spreads_df = odds_spreads_df[odds_spreads_df['team'].isin(matched_names['spreads']['odds'])].sort_values(by='team').reset_index(drop=True)
kalshi_spreads_df = kalshi_spreads_df[kalshi_spreads_df['team'].isin(matched_names['spreads']['kalshi'])].sort_values(by='team').reset_index(drop=True)

odds_totals_df = odds_totals_df[
    odds_totals_df['home_team'].isin(matched_names['totals']['odds']) |
    odds_totals_df['away_team'].isin(matched_names['totals']['odds'])
].sort_values(by='home_team').reset_index(drop=True)
kalshi_totals_df = kalshi_totals_df[
    (kalshi_totals_df['home_team'].isin(matched_names['totals']['kalshi'])) | 
    (kalshi_totals_df['away_team'].isin(matched_names['totals']['kalshi']))
    ].sort_values(by='home_team').reset_index(drop=True)

# Concatenate winners df

# Specify the columns to extract
kalshi_cols = ['ticker', 'yes_bid', 'yes_ask', 'no_bid', 'no_ask', 'home_team', 'away_team']
odds_cols = ['market', 'start_time', 'team', 'home_team', 'away_team', 'avg_fair_prb']

# Rename overlapping columns in odds to prevent clashes
odds_subset = odds_winners_df[odds_cols].rename(columns={
    'home_team': 'odds_home_team',
    'away_team': 'odds_away_team'
})

kalshi_subset = kalshi_winners_df[kalshi_cols].rename(columns={
    'home_team': 'kalshi_home_team',
    'away_team': 'kalshi_away_team'
})

combined_rows = []
len_matched = len(matched_names['h2h']['kalshi'])
matched_names_h2h = matched_names['h2h']

for i in range(len_matched):
    odds_name = matched_names_h2h['odds'][i]
    kalshi_name = matched_names_h2h['kalshi'][i]

    # Find the corresponding odds row
    odds_row = odds_subset.loc[odds_subset['team'] == odds_name]
    assert len(odds_row) == 1, f"Expected one row for {odds_name}, got {len(odds_row)}"

    # Find the two matching Kalshi rows
    kalshi_rows = kalshi_subset.loc[
        (kalshi_subset['kalshi_home_team'] == kalshi_name) |
        (kalshi_subset['kalshi_away_team'] == kalshi_name)
    ]
    assert len(kalshi_rows) == 2, f"Expected two rows for {kalshi_name}, got {len(kalshi_rows)}"

    # Extract rows
    k1 = kalshi_rows.iloc[0]
    k2 = kalshi_rows.iloc[1]
    midprice1 = (k1['yes_bid'] + k1['yes_ask']) / 2
    midprice2 = (k2['yes_bid'] + k2['yes_ask']) / 2

    # Extract scalar fair probability
    prb = odds_row['avg_fair_prb'].astype(float).item()

    # Choose the row closer to the odds probability
    if ((midprice1 - prb) ** 2) < ((midprice2 - prb) ** 2):
        combined_row = pd.concat([k1, odds_row.iloc[0]])
    else:
        combined_row = pd.concat([k2, odds_row.iloc[0]])

    combined_rows.append(combined_row)

combined_winners_df = pd.DataFrame(combined_rows).sort_values(by='odds_home_team')
combined_winners_df = combined_winners_df.reset_index(drop=True)

# ================================ 
# =======  Calculate Bets  ========
# ================================ 

midprice = (combined_winners_df['yes_bid'] + combined_winners_df['yes_ask']) / 2

edge_winners_df = combined_winners_df.loc[
    (combined_winners_df['avg_fair_prb'] >= midprice + CONFIG.WINNERS_EDGE) |
    (combined_winners_df['avg_fair_prb'] <= midprice - CONFIG.WINNERS_EDGE)
].reset_index(drop=True)

edge_winners_df = edge_winners_df.loc[((edge_winners_df['avg_fair_prb'] > 0.15) & (edge_winners_df['avg_fair_prb'] < 0.49)) |
                                      ((edge_winners_df['avg_fair_prb'] > 0.51) & (edge_winners_df['avg_fair_prb'] < 0.85)) ]

midprice_yes = (edge_winners_df['yes_bid'] + edge_winners_df['yes_ask']) / 2
midprice_no = (edge_winners_df['no_bid'] + edge_winners_df['no_ask']) / 2

q_yes = edge_winners_df['avg_fair_prb']
q_no = 1 - edge_winners_df['avg_fair_prb'] 

edge_winners_df['edge'] = np.where(q_yes > midprice_yes, q_yes - midprice_yes, q_no - midprice_no)

edge_winners_df['buy_direction'] = np.where(q_yes > midprice_yes, "yes", "no")
edge_winners_df['raw_kelly'] = np.where(q_yes > midprice_yes, edge_winners_df['edge'] / (1 - midprice_yes),
                                        edge_winners_df['edge'] / (1 - midprice_no))

total_kelly = edge_winners_df['raw_kelly'].sum() 
if total_kelly >= 1: 
    edge_winners_df['real_kelly'] = pd.DataFrame({
        'original': edge_winners_df['raw_kelly'],
        'normalized': (edge_winners_df['raw_kelly'] / total_kelly)
    }).min(axis=1)

# Define the real_kelly logic
def scale_kelly(row):
    k = row['raw_kelly']
    p = row['avg_fair_prb']
    
    if k == 0 or pd.isna(k):
        return 0
    if 0.05 <= p < 0.25:
        return min(CONFIG.Q1_WEIGHT * k, CONFIG.KELLY_UPPERBOUND)
    elif 0.25 <= p < 0.5:
        return min(CONFIG.Q2_WEIGHT * k, CONFIG.KELLY_UPPERBOUND)
    elif 0.5 <= p < 0.75:
        return min(CONFIG.Q3_WEIGHT * k, CONFIG.KELLY_UPPERBOUND)
    elif 0.75 <= p < 0.95:
        return min(CONFIG.Q4_WEIGHT * k, CONFIG.KELLY_UPPERBOUND)
    else:
        return 0 

# Apply to the DataFrame
edge_winners_df['real_kelly'] = edge_winners_df.apply(scale_kelly, axis=1)
edge_winners_df['optimal_bet'] = edge_winners_df['real_kelly'] * WINNERS_BANKROLL

q = edge_winners_df['avg_fair_prb']
p = midprice_yes

num_contracts = np.where(q > p, edge_winners_df['optimal_bet'] // edge_winners_df['yes_bid'], edge_winners_df['optimal_bet'] // edge_winners_df['no_bid'])
edge_winners_df['num_contracts'] = num_contracts
trading_cost = np.where(q > p, np.ceil(100*(0.0175 * num_contracts * edge_winners_df['yes_bid'] * (1 - edge_winners_df['yes_bid']))) / 100,
                        np.ceil(100*(0.0175 * num_contracts * edge_winners_df['no_bid'] * (1 - edge_winners_df['no_bid']))) / 100)
edge_winners_df['trading_cost'] = trading_cost
profit = np.where(q > p, ((1 - edge_winners_df['yes_bid']) * num_contracts - trading_cost), ((1 - edge_winners_df['no_bid']) *  num_contracts - trading_cost))
edge_winners_df['profit'] = profit
edge_winners_df['ev'] = np.where(q > p, (profit * q_yes - (edge_winners_df['optimal_bet'] + trading_cost) * (1 - q_yes)).round(2), 
                                 (profit * q_no - (edge_winners_df['optimal_bet'] + trading_cost) * (1 - q_no)).round(2))
filtered_winners_df = edge_winners_df.loc[edge_winners_df['ev'] > 0.1].reset_index(drop=True)

s = filtered_winners_df['start_time'].astype(str)
s = s.str.replace(r'\s+[A-Z]{3}$', '', regex=True)
dt = pd.to_datetime(s, errors='coerce')
filtered_winners_df['start_time'] = dt.dt.tz_localize('America/Chicago')

now = datetime.now(pytz.timezone('America/Chicago'))
filtered_winners_df = filtered_winners_df.loc[filtered_winners_df['start_time'] > now].sort_values('odds_home_team').reset_index(drop=True)

dupe_mask = filtered_winners_df['kalshi_home_team'].duplicated(keep=False)
dupes = filtered_winners_df[dupe_mask]
uniques = filtered_winners_df[~dupe_mask]
best_dupes = dupes.loc[dupes.groupby('kalshi_home_team')['ev'].idxmax()]
filtered_winners_df = pd.concat([uniques, best_dupes], ignore_index=True)


team_cols = ['kalshi_home_team', 'kalshi_away_team',
             'odds_home_team', 'odds_away_team']

teams_df = filtered_winners_df[team_cols].copy()

filtered_winners_df = filtered_winners_df.drop(columns=['kalshi_home_team', 'kalshi_away_team']).reset_index(drop=True)
filtered_winners_df[['edge', 'raw_kelly', 'real_kelly']] = filtered_winners_df[['edge', 'raw_kelly', 'real_kelly']].round(4) * 100

# ================================ 
# ======= Find Spread Bets ======= 
# ================================ 

kalshi_cols = ['ticker', 'yes_bid', 'yes_ask', 'no_bid', 'no_ask', 'team', 'points', 'yes_spread', 'no_spread']
odds_cols = ['market', 'start_time', 'team', 'home_team', 'away_team', 'avg_fair_prb', 'point']

odds_subset = odds_spreads_df[odds_cols].rename(columns={
    'home_team': 'odds_home_team',
    'away_team': 'odds_away_team',
    'team': 'odds_team'
})

kalshi_subset = kalshi_spreads_df[kalshi_cols].copy()
kalshi_subset['midprice'] = (kalshi_subset['yes_bid'] + kalshi_subset['yes_ask']) / 2
kalshi_subset = kalshi_subset.loc[kalshi_subset['yes_spread'] <= 0.05]

combined_rows = []

for _, kalshi_row in kalshi_subset.iterrows():
    kalshi_team = kalshi_row['team']
    for _, odds_row in odds_subset.iterrows():
        odds_row = odds_row.copy()
        odds_team = odds_row['odds_team']
        if (kalshi_team in odds_team):
            if ((abs(odds_row['point']) == kalshi_row['points']) & (odds_row['avg_fair_prb'] > kalshi_row['midprice'])) or (
                (abs(odds_row['point']) > kalshi_row['points']) & (odds_row['avg_fair_prb'] >= kalshi_row['midprice'])):
                odds_row['buy_direction'] = "yes"
                combined_row = pd.concat([kalshi_row, odds_row])
                combined_rows.append(combined_row)
            elif ((abs(odds_row['point']) == kalshi_row['points']) & (odds_row['avg_fair_prb'] < kalshi_row['midprice'])) or (
                (abs(odds_row['point']) < kalshi_row['points']) & (odds_row['avg_fair_prb'] <= kalshi_row['midprice'])):
                odds_row['buy_direction'] = "no"
                combined_row = pd.concat([kalshi_row, odds_row])
                combined_rows.append(combined_row)

combined_spreads_df = pd.DataFrame(combined_rows).drop(columns=['team', 'market']).rename(
    columns={'odds_team': 'team', 'points': 'kalshi_pts', 'point': 'odds_pts'}).drop_duplicates(subset=['ticker', 'kalshi_pts', 'odds_pts'])                        
combined_spreads_df = combined_spreads_df.reset_index(drop=True)                    

midprice = (combined_spreads_df['yes_bid'] + combined_spreads_df['yes_ask']) / 2

edge_spreads_df = combined_spreads_df.loc[
    (combined_spreads_df['avg_fair_prb'] >= midprice + CONFIG.SPREAD_EDGE) |
    (combined_spreads_df['avg_fair_prb'] <= midprice - CONFIG.SPREAD_EDGE)
].reset_index(drop=True)

midprice_yes = (edge_spreads_df['yes_bid'] + edge_spreads_df['yes_ask']) / 2
midprice_no = (edge_spreads_df['no_bid'] + edge_spreads_df['no_ask']) / 2

q_yes = edge_spreads_df['avg_fair_prb']
q_no = 1 - edge_spreads_df['avg_fair_prb'] 

edge_spreads_df['edge'] = np.where(q_yes > midprice_yes, q_yes - midprice_yes, q_no - midprice_no)

edge_spreads_df['raw_kelly'] = np.where(q_yes > midprice_yes, edge_spreads_df['edge'] / (1 - midprice_yes),
                                        edge_spreads_df['edge'] / (1 - midprice_no))

total_kelly = edge_spreads_df['raw_kelly'].sum() 
if total_kelly >= 1: 
    edge_spreads_df['real_kelly'] = pd.DataFrame({
        'original': edge_spreads_df['raw_kelly'],
        'normalized': (edge_spreads_df['raw_kelly'] / total_kelly)
    }).min(axis=1)

# Define the real_kelly logic
def scale_kelly(row):
    k = row['raw_kelly']
    p = row['avg_fair_prb']
    
    if k == 0 or pd.isna(k):
        return 0
    if 0.05 <= p < 0.25:
        return min(CONFIG.Q1_WEIGHT * k, CONFIG.KELLY_UPPERBOUND)
    elif 0.25 <= p < 0.5:
        return min(CONFIG.Q2_WEIGHT * k, CONFIG.KELLY_UPPERBOUND)
    elif 0.5 <= p < 0.75:
        return min(CONFIG.Q3_WEIGHT * k, CONFIG.KELLY_UPPERBOUND)
    elif 0.75 <= p < 0.95:
        return min(CONFIG.Q4_WEIGHT * k, CONFIG.KELLY_UPPERBOUND)
    else:
        return 0 

# Apply to the DataFrame
edge_spreads_df['real_kelly'] = edge_spreads_df.apply(scale_kelly, axis=1)
edge_spreads_df['optimal_bet'] = edge_spreads_df['real_kelly'] * SPREADS_BANKROLL

q = edge_spreads_df['avg_fair_prb']
p = midprice_yes

num_contracts = np.where(q > p, edge_spreads_df['optimal_bet'] // edge_spreads_df['yes_bid'], edge_spreads_df['optimal_bet'] // edge_spreads_df['no_bid'])
edge_spreads_df['num_contracts'] = num_contracts
trading_cost = np.where(q > p, np.ceil(100*(0.0175 * num_contracts * edge_spreads_df['yes_bid'] * (1 - edge_spreads_df['yes_bid']))) / 100,
                        np.ceil(100*(0.0175 * num_contracts * edge_spreads_df['no_bid'] * (1 - edge_spreads_df['no_bid']))) / 100)
edge_spreads_df['trading_cost'] = trading_cost
profit = np.where(q > p, ((1 - edge_spreads_df['yes_bid']) * num_contracts - trading_cost), ((1 - edge_spreads_df['no_bid']) *  num_contracts - trading_cost))
edge_spreads_df['profit'] = profit
edge_spreads_df['ev'] = np.where(q > p, (profit * q_yes - (edge_spreads_df['optimal_bet'] + trading_cost) * (1 - q_yes)).round(2), 
                                 (profit * q_no - (edge_spreads_df['optimal_bet'] + trading_cost) * (1 - q_no)).round(2))
filtered_spreads_df = edge_spreads_df.loc[edge_spreads_df['ev'] > 0.10].reset_index(drop=True)

s = filtered_spreads_df['start_time'].astype(str)
s = s.str.replace(r'\s+[A-Z]{3}$', '', regex=True)
dt = pd.to_datetime(s, errors='coerce')
filtered_spreads_df['start_time'] = dt.dt.tz_localize('America/Chicago')

now = datetime.now(pytz.timezone('America/Chicago'))
filtered_spreads_df = filtered_spreads_df.loc[filtered_spreads_df['start_time'] > now].sort_values('team').reset_index(drop=True)

filtered_spreads_df = filtered_spreads_df.drop(columns=['odds_home_team', 'odds_away_team', 'yes_spread', 'no_spread'])
filtered_spreads_df[['edge', 'raw_kelly', 'real_kelly']] = filtered_spreads_df[['edge', 'raw_kelly', 'real_kelly']].round(4) * 100
# =========================
# ========= MAIN ==========
# =========================


def parse_cli_args():
    parser = argparse.ArgumentParser(description="Kalshi auto trader")
    parser.add_argument("--live", action="store_true", help="Send real orders (disable DRY_RUN)")
    parser.add_argument("--api-key", dest="api_key", help="Override CONFIG.API_KEY or use env KALSHI_API_KEY")
    parser.add_argument("--host", dest="host", help="Override CONFIG.HOST or use env KALSHI_HOST")
    return parser.parse_args()


def select_working_host(preferred: Optional[str]):
    tried = []
    candidates = []
    if preferred:
        candidates.append(preferred)
    candidates.append(CONFIG.HOST)
    candidates.extend(getattr(CONFIG, "HOST_FALLBACKS", []))
    seen = set()
    candidates = [h for h in candidates if not (h in seen or seen.add(h))]

    for host in candidates:
        CONFIG.HOST = host
        payload = http_request("GET", "markets", params=None)
        if payload and payload.get("markets") is not None:
            print(f"🌐 Using host: {host}")
            return
        tried.append(host)
    sys.exit(f"❌ Could not reach any host. Tried: {', '.join(tried)}")


def main():
    args = parse_cli_args()

    env_host = os.getenv("KALSHI_HOST")
    if args.host:
        CONFIG.HOST = args.host
    elif env_host:
        CONFIG.HOST = env_host

    env_key = os.getenv("KALSHI_API_KEY")
    if args.api_key:
        CONFIG.API_KEY = args.api_key
    elif env_key:
        CONFIG.API_KEY = env_key

    if args.live:
        CONFIG.DRY_RUN = False
    print("🧪 DRY-RUN mode: orders will be echoed locally and not sent." if CONFIG.DRY_RUN else "🚨 LIVE mode: orders will be sent to Kalshi.")

    if not CONFIG.API_KEY:
        sys.exit("❌ Please set a valid API key via CONFIG or --api-key/KALSHI_API_KEY.")

    select_working_host(args.host or env_host)

    # Place orders based on dataframes.
    process_dataframe(filtered_winners_df, "winners")
    process_dataframe(filtered_spreads_df, "spreads")

    print("✅ Completed processing; exiting.")


if __name__ == "__main__":
    main()
