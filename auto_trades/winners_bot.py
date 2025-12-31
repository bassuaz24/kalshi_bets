import pandas as pd
import numpy as np
from datetime import datetime, timezone
import matplotlib.pyplot as plt
import regex as re
import math
from collections import defaultdict
import pytz
import shin
import os
import sys
import uuid
import requests
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import csv
import time

from scipy.stats import norm
from scipy.optimize import brentq
from scipy.special import expit, logit

from rapidfuzz.fuzz import ratio

try:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
except ImportError:
    sys.exit("❌ Missing dependency 'cryptography'. Install with: pip install cryptography")


except ImportError:
    sys.exit("❌ Missing dependency 'cryptography'. Install with: pip install cryptography")

# --- CONFIGURATION ---
class CONFIG:
    HOST = "https://trading-api.kalshi.com"
    API_KEY = os.environ.get("KALSHI_API_KEY")
    PRIVATE_KEY_PATH = os.environ.get("KALSHI_PRIVATE_KEY_PATH")
    AUTH_MODE = "kalshi_pss"
    DRY_RUN = True # Set to False to place real trades
    
    @classmethod
    def get_date(cls):
        return date

# --- TRADING & API UTILITIES ---
def now_iso() -> str: return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
def clamp_price(p: float) -> Optional[float]:
    if p is None or np.isnan(p): return None
    return round(max(0.01, min(0.99, p)), 2)

def log_order(row_data: Dict[str, Any], fieldnames: List[str], date: str):
    output_dir = 'daily_reports'
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, f'trades_{date}.csv')
    is_new = not os.path.exists(output_path)
    with open(output_path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if is_new: writer.writeheader()
        writer.writerow(row_data)

def _load_private_key(path: str):
    with open(path, "rb") as key_file:
        return serialization.load_pem_private_key(key_file.read(), password=None, backend=default_backend())

def _sign_pss_text(private_key, text: str) -> str:
    message = text.encode("utf-8")
    signature = private_key.sign(message, padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH), hashes.SHA256())
    import base64
    return base64.b64encode(signature).decode("utf-8")

def _headers(method: str, path: str) -> Dict[str, str]:
    base = {"Accept": "application/json", "Content-Type": "application/json"}
    priv_path = os.path.expanduser(CONFIG.PRIVATE_KEY_PATH)
    ts = str(int(time.time() * 1000))
    path_clean = "/trade-api/v2/" + path.lstrip("/").split("?")[0]
    msg = ts + method.upper() + path_clean
    priv = _load_private_key(priv_path)
    sig = _sign_pss_text(priv, msg)
    base.update({"KALSHI-ACCESS-KEY": CONFIG.API_KEY, "KALSHI-ACCESS-SIGNATURE": sig, "KALSHI-ACCESS-TIMESTAMP": ts})
    return base

def http_request(method: str, path: str, body: Optional[dict] = None) -> Optional[dict]:
    url = f"{CONFIG.HOST.rstrip('/')}/trade-api/v2/{path.lstrip('/')}"
    data = json.dumps(body) if body is not None else None
    if CONFIG.DRY_RUN and method.upper() in {"POST", "DELETE"}:
        #print(f"DRY RUN: {method} {url} {data}")
        return {"dry_run": True, "order": {"ticker": body.get("ticker"), "client_order_id": body.get("client_order_id")}}
    try:
        r = requests.request(method.upper(), url, headers=_headers(method=method, path=path), data=data, timeout=15)
        if r.status_code in (200, 201): return r.json()
        print(f"❌ {method} {url} {r.status_code}: {r.text[:300]}")
    except requests.RequestException as e:
        print(f"⚠️ {method} {url} error: {e}")
    return None

def place_order(payload: Dict[str, Any]) -> Optional[dict]:
    return http_request("POST", "portfolio/orders", body=payload)

def submit_order(row: pd.Series, date: str):
    price = clamp_price(row.get('ask'))
    if price is None: return

    payload = {
        "ticker": row.get("ticker"),
        "side": row.get("buy_direction"),
        "action": "buy",
        "type": "limit",
        "count": int(row.get("num_contracts")),
        "client_order_id": str(uuid.uuid4()),
        "yes_price": int(price * 100) if row.get("buy_direction") == 'yes' else None,
        "no_price": int(price * 100) if row.get("buy_direction") == 'no' else None,
    }
    payload = {k: v for k, v in payload.items() if v is not None}

    resp = place_order(payload)
    
    # --- Logging Logic ---
    log_data_full = row.to_dict()
    log_data_full['timestamp'] = now_iso()
    
    desired_columns = [
        'timestamp', 'ticker', 'ask', 'avg_fair_prb', 'edge', 
        'buy_direction', 'tp', 'sl', 'kelly', 'optimal_bet', 
        'num_contracts', 'ev'
    ]
    
    # Create a new dictionary with only the desired columns
    filtered_log_data = {key: log_data_full.get(key) for key in desired_columns}

    log_order(filtered_log_data, desired_columns, date)
    
    mode = "LIVE" if not CONFIG.DRY_RUN else "DRY-RUN"
    if resp and (resp.get("dry_run") or resp.get("order")):
        print(f"🪙 {mode} order: {row.get('ticker')} side={row.get('buy_direction')} size={int(row.get('num_contracts'))} price={price:.2f} tp={row.get('tp'):.2f} sl={row.get('sl'):.2f}")
    else:
        print(f"❌ order failed for {row.get('ticker')}")

date = '2025-12-16'
odds_sport = 'cbbm' #cbbm, cbbm2, cbbw2, cfb, cfb2, nba, nfl
kalshi_sport = 'ncaab' #ncaab, ncaabw, ncaaf, nba, nfl

EDGE = 0.01
KELLY_UPPERBOUND = 1
BANKROLL = 400.00
Q1_WEIGHT = 1.00
Q2_WEIGHT = 1.00
Q3_WEIGHT = 1.00
Q4_WEIGHT = 1.00

#betus good for nba, pinnacle, betonline best for everything, fanduel pretty good

odds_df = pd.read_csv(f"../data_collection/updated_scripts/oddsapi_outputs/{date}/{odds_sport}_odds.csv")
odds_df.drop(columns=['league'], inplace=True)
odds_df.rename(columns={'price': 'odds'}, inplace=True)

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


# Average per-team fair probabilities across DraftKings/FanDuel/Pinnacle for winners_df
WEIGHTS = {
    "Pinnacle": 0.7,
    "BetOnline.ag": 0.1,
    "BetUS": 0.1,
    "FanDuel": 0.1
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


kalshi_winners_df = pd.read_csv(f"../data_collection/updated_scripts/kalshi_data_logs/{date}/{kalshi_sport}_winners.csv")


columns_to_drop = ['timestamp', 'market_type']
kalshi_winners_df.drop(columns=columns_to_drop, inplace=True)

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

matched_names = {
    'h2h': {
        'kalshi': matched_kalshi_h2h,
        'odds': matched_odds_h2h
    }
}
assert(len(matched_names['h2h']['kalshi']) == len(matched_names['h2h']['odds']))

odds_winners_df = odds_winners_df[
    odds_winners_df['home_team'].isin(matched_names['h2h']['odds']) |
    odds_winners_df['away_team'].isin(matched_names['h2h']['odds'])
].drop_duplicates(subset='team').sort_values(by='home_team').reset_index(drop=True)

kalshi_winners_df = kalshi_winners_df[
    kalshi_winners_df['home_team'].isin(matched_names['h2h']['kalshi']) |
    kalshi_winners_df['away_team'].isin(matched_names['h2h']['kalshi'])
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


midprice = (combined_winners_df['yes_bid'] + combined_winners_df['yes_ask']) / 2

edge_winners_df = combined_winners_df.loc[
    (combined_winners_df['avg_fair_prb'] >= combined_winners_df['yes_ask'] + EDGE) |
    ((1 - combined_winners_df['avg_fair_prb']) >= combined_winners_df['no_ask'] + EDGE)
].reset_index(drop=True)

midprice = (edge_winners_df['yes_bid'] + edge_winners_df['yes_ask']) / 2
edge_winners_df['midprice'] = midprice

edge_winners_df = edge_winners_df.loc[((edge_winners_df['avg_fair_prb'] > 0.15) & (edge_winners_df['avg_fair_prb'] < 0.49)) |
                                      ((edge_winners_df['avg_fair_prb'] > 0.51) & (edge_winners_df['avg_fair_prb'] < 0.85)) ]

midprice_yes = (edge_winners_df['yes_bid'] + edge_winners_df['yes_ask']) / 2
midprice_no = (edge_winners_df['no_bid'] + edge_winners_df['no_ask']) / 2

q_yes = edge_winners_df['avg_fair_prb']
q_no = 1 - edge_winners_df['avg_fair_prb'] 

#edge_winners_df['edge'] = np.where(q_yes > midprice_yes, q_yes - midprice_yes, q_no - midprice_no)
edge_winners_df['edge'] = np.where(q_yes > midprice_yes, q_yes - edge_winners_df['yes_ask'], q_no - edge_winners_df['no_ask'])

edge_winners_df['avg_fair_prb'] = np.where(q_yes > midprice_yes, edge_winners_df['avg_fair_prb'], 1 - edge_winners_df['avg_fair_prb'])

edge_winners_df['bid'] = np.where(q_yes > midprice_yes, edge_winners_df['yes_bid'], edge_winners_df['no_bid'])
edge_winners_df['ask'] = np.where(q_yes > midprice_yes, edge_winners_df['yes_ask'], edge_winners_df['no_ask'])

edge_winners_df['buy_direction'] = np.where(q_yes > midprice_yes, "yes", "no")
edge_winners_df.reset_index(drop=True, inplace=True)

assert (len(edge_winners_df) != 0), f"No bets on {odds_sport} today"

def compute_hitting_prob(row, sl, tp):
    """ 
    Assuming biased or unbiased random walk based on the fraction of previous month's
    up / total step count 
    """
    prob = row['avg_fair_prb']
    rv = prob
    if (prob >= 0.4) & (prob <= 0.6):
        rv = (prob - sl) / (tp - sl)
        return rv
    elif (prob >=0.3) & (prob < 0.4):
        p = None
        q = None
    elif (prob >= 0.2) & (prob < 0.3):
        p = None
        q = None
    elif (prob >= 0.10) & (prob < 0.2):
        p = None
        q = None
    elif (prob > 0.6) & (prob <= 0.7):
        p = None
        q = None
    elif (prob > 0.7) & (prob <= 0.8):
        p = None
        q = None
    elif (prob > 0.8) & (prob <= 0.9):
        p = None
        q = None
    rv = (1 - ((q / p) ** (prob - sl))) / (1 - ((q / p) ** (tp - sl)))
    return rv

KELLY_FRAC = 0.33

ev_dict = defaultdict(list)
for i in range(len(edge_winners_df)):
    row = edge_winners_df.iloc[i]
    #entry = row['bid']
    entry = row['ask']
    tp_list = []
    sl_list = []
    temp_tp = row['avg_fair_prb'] + 0.1
    temp_sl = entry - 0.1
    while (temp_tp < 1):
        tp_list.append(temp_tp)
        temp_tp = temp_tp + 0.01
    assert(len(tp_list) != 0)
    while (temp_sl > 0):
        sl_list.append(temp_sl)
        temp_sl = temp_sl - 0.01
    if len(sl_list) == 0:
        sl_list.append(0)
    for tp in tp_list:
        for sl in sl_list:
            if tp <= sl:
                continue
            if not (sl < entry < tp):
                continue
            if not (sl < row['avg_fair_prb'] < tp):
                continue
           
            #p = compute_hitting_prob(row, sl, tp)
            p = (row['avg_fair_prb'] - sl) / (tp - sl)
            kelly = entry * (p * (tp - entry) - (1 - p) * (entry - sl)) / ((tp - entry) * (entry - sl))
            kelly = kelly * KELLY_FRAC
            optimal_bet = kelly * BANKROLL
            num_contracts = optimal_bet // entry
            trading_cost_entry = np.ceil(100*(0.0175 * num_contracts * entry * (1 - entry))) / 100
            trading_cost_exit1 = np.ceil(100*(0.0175 * num_contracts * tp * (1 - tp))) / 100
            trading_cost_exit2 = np.ceil(100*(0.0175 * num_contracts * sl * (1 - sl))) / 100
            trading_cost_exit = (trading_cost_exit1 + trading_cost_exit2) / 2
            trading_cost = trading_cost_entry + trading_cost_exit
            profit = num_contracts * (tp - entry)
            loss = num_contracts * (entry - sl)
            ev = profit * p - loss * (1 - p) - trading_cost
            ev_dict[i].append([tp, sl, kelly, optimal_bet, num_contracts, trading_cost, profit, loss, ev])
        
cols = ['tp', 'sl', 'kelly', 'optimal_bet', 'num_contracts', 'trading_cost', 'profit', 'loss', 'ev']

best_by_key = {
    k: max(trades, key=lambda x: x[8])   
    for k, trades in ev_dict.items()
    if trades
}

best_by_key = {
    k: [round(v[0], 2), round(v[1], 2), *v[2:]]
    for k, v in best_by_key.items()
}

keys = list(best_by_key.keys())
filtered_winners_df = edge_winners_df.iloc[keys].copy()
filtered_winners_df[cols] = np.array([best_by_key[k] for k in keys], dtype=float)

filtered_winners_df.drop(columns=['yes_bid','yes_ask', 'no_bid', 'no_ask', 'market', 'midprice'], inplace=True)
filtered_winners_df = filtered_winners_df[['ticker', 'start_time', 'kalshi_home_team', 'kalshi_away_team',
       'odds_home_team', 'odds_away_team', 'team', 'ask', 'avg_fair_prb', 'edge', 'buy_direction',
       'tp', 'sl', 'kelly', 'optimal_bet', 'num_contracts', 'trading_cost',
       'profit', 'loss', 'ev']]
filtered_winners_df['edge'] = filtered_winners_df['edge'] * 100
filtered_winners_df['optimal_bet'] = filtered_winners_df['optimal_bet'].round(2)

s = filtered_winners_df['start_time'].astype(str)
s = s.str.replace(r'\s+[A-Z]{3}$', '', regex=True)
dt = pd.to_datetime(s, errors='coerce')
filtered_winners_df['start_time'] = dt.dt.tz_localize('America/Chicago')

now = datetime.now(pytz.timezone('America/Chicago'))
#filtered_winners_df = filtered_winners_df.loc[filtered_winners_df['start_time'] > now].sort_values('odds_home_team').reset_index(drop=True)

dupe_mask = filtered_winners_df['kalshi_home_team'].duplicated(keep=False)
dupes = filtered_winners_df[dupe_mask]
uniques = filtered_winners_df[~dupe_mask]
best_dupes = dupes.loc[dupes.groupby('kalshi_home_team')['ev'].idxmax()]
filtered_winners_df = pd.concat([uniques, best_dupes], ignore_index=True)
filtered_winners_df.drop(columns=['start_time'], inplace=True)

# --- Execute Trades ---
print("\n--- Final Trades ---")
print(filtered_winners_df)
print("\nPlacing trades...")
for _, row in filtered_winners_df.iterrows():
    submit_order(row, date)

print(f"\nTrades processed and logged to daily_reports/trades_{date}.csv")
