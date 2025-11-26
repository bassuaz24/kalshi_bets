#!/usr/bin/env python3
"""
live_trader_v2
--------------
Rebuilds filtered_winners_df and filtered_spreads_df using the exact logic from
data_analysis.ipynb, then places orders based on those dataframes. Orders use
the num_contracts and yes_bid from each row (direction inferred from fair vs.
bid/ask). Ends after processing all rows.
"""

import argparse
import csv
import json
import math
import os
import sys
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

try:
    import pandas as pd
    import numpy as np
except ImportError as e:
    sys.exit(f"❌ Missing dependency: {e}. Install pandas and numpy.")

try:
    from rapidfuzz.fuzz import ratio as fuzz_ratio
except ImportError:
    from difflib import SequenceMatcher

    def fuzz_ratio(a, b):
        return int(100 * SequenceMatcher(None, str(a), str(b)).ratio())


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
    API_KEY = "KALSHI_API_KEY"
    AUTH_MODE = "kalshi_pss"
    PRIVATE_KEY_PATH = None

    DRY_RUN = True
    REQ_TIMEOUT = 15
    MAX_RETRIES = 3
    RETRY_SLEEP = 1.0

    MIN_TICK = 0.01
    MIN_PRICE = 0.01
    MAX_PRICE = 0.99

    LOG_DIR = "live_betting"

    class DATA:
        DATE = "2025-11-26"  # YYYY-MM-DD; if None, use today
        ODDS_SPORT = "cbb"
        KALSHI_SPORT = "ncaab"
        ODDS_DIR = "data_collection/updated_scripts/oddsapi_outputs"
        KALSHI_DIR = "data_collection/updated_scripts/kalshi_data_logs"
        OUTPUT_DIR = "live_betting/analysis_outputs"
        EDGE_WINNERS = 0.00
        EDGE_SPREADS = 0.01
        WINNERS_EV_THRESHOLD = 0.15
        SPREADS_EV_THRESHOLD = 0.0
        TOTAL_BANKROLL = 200  # None => pull from account
        WINNERS_PROPORTION = 0.75
        SPREADS_PROPORTION = 1.0 - WINNERS_PROPORTION
        KELLY_CAP = 1.0
        Q1_WEIGHT = 1.0
        Q2_WEIGHT = 1.0
        Q3_WEIGHT = 1.0
        Q4_WEIGHT = 1.0


REPO_ROOT = Path(__file__).resolve().parent.parent


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
    log_csv("orders_log.csv", fields, row)


def log_health(msg: str, **kw):
    row = {"ts": now_iso(), "msg": msg}
    row.update(kw)
    fields = ["ts", "msg"] + sorted([k for k in row.keys() if k not in {"ts", "msg"}])
    log_csv("health_log.csv", fields, row)


# =========================
# ===== HTTP / CLIENT =====
# =========================


def _load_private_key(path: str):
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.backends import default_backend
    except ImportError:
        sys.exit("❌ Missing dependency 'cryptography'. Install with: pip install cryptography")
    with open(path, "rb") as key_file:
        return serialization.load_pem_private_key(key_file.read(), password=None, backend=default_backend())


def _sign_pss_text(private_key, text: str) -> str:
    try:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding
    except ImportError:
        sys.exit("❌ Missing dependency 'cryptography'. Install with: pip install cryptography")
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
        "User-Agent": "kalshi-live-trader-v2/1.0",
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
        sys.exit("❌ Only kalshi_pss auth supported in v2.")

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
# === NOTEBOOK LOGIC ====== <-- Bug is probably here, compare code here to data analysis 
# =========================


def _latest_csv(base_dir: Path, sport_prefix: str, suffix: str, date_hint: str) -> Optional[Path]:
    search_base = base_dir / str(date_hint)
    if not search_base.exists():
        return None
    candidates = list(search_base.rglob(f"{sport_prefix}_{suffix}*.csv"))
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _clean_team_name(name: Optional[str]) -> Optional[str]:
    if name is None or not isinstance(name, str):
        return None
    import re

    return re.sub(r"\bSt\.$", "St", name.strip())


def build_filtered_frames(date_str: str, bankroll_winners: float, bankroll_spreads: float):
    odds_dir = REPO_ROOT / CONFIG.DATA.ODDS_DIR
    kalshi_dir = REPO_ROOT / CONFIG.DATA.KALSHI_DIR

    odds_path = _latest_csv(odds_dir, CONFIG.DATA.ODDS_SPORT, "odds", date_str)
    winners_path = _latest_csv(kalshi_dir, CONFIG.DATA.KALSHI_SPORT, "winners", date_str)
    spreads_path = _latest_csv(kalshi_dir, CONFIG.DATA.KALSHI_SPORT, "spreads", date_str)

    if not odds_path or not winners_path:
        sys.exit(f"❌ Missing required CSVs for date={date_str}. odds={odds_path} winners={winners_path}")

    odds_df = pd.read_csv(odds_path)
    if "league" in odds_df.columns:
        odds_df = odds_df.drop(columns=["league"])
    odds_df = odds_df.rename(columns={"price": "odds"})
    odds_df["odds"] = pd.to_numeric(odds_df["odds"], errors="coerce")
    odds_df["point"] = pd.to_numeric(odds_df.get("point"), errors="coerce")
    odds_df["vig_prob"] = 1 / odds_df["odds"]

    def remove_vig_probs(df):
        df = df.copy()
        df["fair_prb"] = pd.NA
        grouped = df.groupby(["game_id", "bookmaker", "market"])
        for _, group in grouped:
            if len(group) < 2:
                continue
            probs = group["vig_prob"]
            total = probs.sum()
            if total and total > 0:
                fair_probs = (probs / total).round(4)
                df.loc[group.index, "fair_prb"] = fair_probs
        return df

    odds_df = remove_vig_probs(odds_df)

    odds_winners_df = odds_df[odds_df["market"] == "h2h"].copy()
    odds_spreads_df = odds_df[odds_df["market"] == "spreads"].copy()
    odds_spreads_df = odds_spreads_df.loc[(odds_spreads_df["point"].notna()) & (odds_spreads_df["point"] > 0)]
    odds_totals_df = odds_df[odds_df["market"] == "totals"].copy()

    for _df in (odds_winners_df, odds_spreads_df, odds_totals_df):
        if "fair_prb" in _df.columns:
            _df["fair_prb"] = pd.to_numeric(_df["fair_prb"], errors="coerce")

    mask = odds_winners_df["fair_prb"].notna()
    avg_by_team = (
        odds_winners_df.loc[mask]
        .groupby(["game_id", "team"])["fair_prb"]
        .transform("median")
        .round(4)
    )
    odds_winners_df.loc[mask, "avg_fair_prb"] = avg_by_team
    odds_winners_df.loc[~mask, "avg_fair_prb"] = pd.NA

    mask = odds_spreads_df["fair_prb"].notna()
    avg_by_point = (
        odds_spreads_df.loc[mask]
        .groupby(["game_id", "point", "team"])["fair_prb"]
        .transform("mean")
        .round(4)
    )
    odds_spreads_df["avg_fair_prb"] = avg_by_point

    mask = odds_totals_df["fair_prb"].notna()
    avg_by_tot_point = (
        odds_totals_df.loc[mask]
        .groupby(["game_id", "point", "team"])["fair_prb"]
        .transform("mean")
        .round(4)
    )
    odds_totals_df["avg_fair_prb"] = avg_by_tot_point

    kalshi_winners_df = pd.read_csv(winners_path)
    kalshi_spreads_df = pd.read_csv(spreads_path) if spreads_path else pd.DataFrame()

    columns_to_drop = [
        "timestamp",
        "market_type",
        "yes_bid2",
        "yes_ask2",
        "no_bid2",
        "no_ask2",
        "yes_depth_bids",
        "yes_depth_asks",
        "no_depth_bids",
        "no_depth_asks",
    ]
    kalshi_winners_df = kalshi_winners_df.drop(columns=[c for c in columns_to_drop if c in kalshi_winners_df.columns])
    if not kalshi_spreads_df.empty:
        kalshi_spreads_df = kalshi_spreads_df.drop(columns=[c for c in columns_to_drop if c in kalshi_spreads_df.columns])

    for col in ["yes_bid", "yes_ask", "no_bid", "no_ask"]:
        if col in kalshi_winners_df.columns:
            kalshi_winners_df[col] = pd.to_numeric(kalshi_winners_df[col], errors="coerce")
        if not kalshi_spreads_df.empty and col in kalshi_spreads_df.columns:
            kalshi_spreads_df[col] = pd.to_numeric(kalshi_spreads_df[col], errors="coerce")

    def extract_teams_from_winners(title):
        if not isinstance(title, str):
            return pd.Series([None, None])
        title = title.replace(" Winner?", "")
        if " at " in title:
            right, left = title.split(" at ", 1)
        elif " vs " in title:
            right, left = title.split(" vs ", 1)
        else:
            return pd.Series([None, None])
        return pd.Series([_clean_team_name(left), _clean_team_name(right)])

    kalshi_winners_df[["home_team", "away_team"]] = kalshi_winners_df["title"].apply(extract_teams_from_winners)

    kalshi_spreads_df["team"] = (
        kalshi_spreads_df["title"].apply(
            lambda t: _clean_team_name(t.split(" wins by ", 1)[0]) if isinstance(t, str) and " wins by " in t else None
        )
        if not kalshi_spreads_df.empty
        else pd.Series(dtype=object)
    )

    if CONFIG.DATA.KALSHI_SPORT == "ncaaf":
        kalshi_spreads_df["points"] = (
            kalshi_spreads_df["title"].str.extract(r"over ([\d.]+) points\?").astype(float)
            if not kalshi_spreads_df.empty
            else pd.Series(dtype=float)
        )
    elif CONFIG.DATA.KALSHI_SPORT in {"ncaab", "nba"}:
        kalshi_spreads_df["points"] = (
            kalshi_spreads_df["title"].str.extract(r"over ([\d.]+) Points\?").astype(float)
            if not kalshi_spreads_df.empty
            else pd.Series(dtype=float)
        )

    kalshi_winners_teams = pd.unique(kalshi_winners_df.drop_duplicates(subset=["home_team", "away_team"])[["home_team", "away_team"]].values.ravel())
    kalshi_spreads_teams = kalshi_spreads_df["team"].drop_duplicates().tolist() if not kalshi_spreads_df.empty else []

    odds_teams_by_market = odds_df.groupby("market")["team"].unique().to_dict()

    def fuzzy_match_pairs(kalshi_teams, odds_team_names):
        pairs = []
        kalshi_sorted = sorted([k for k in kalshi_teams if isinstance(k, str)], key=lambda x: x[0] if x else "")
        remaining_odds = sorted([o for o in odds_team_names.tolist() if isinstance(o, str)])
        for odds_name in remaining_odds:
            candidates = []
            for kalshi_name in kalshi_sorted:
                if kalshi_name and kalshi_name in odds_name:
                    candidates.append(kalshi_name)
            if not candidates:
                continue
            best_fit = candidates[0]
            best_ratio = fuzz_ratio(best_fit, odds_name)
            for name in candidates:
                curr_ratio = fuzz_ratio(name, odds_name)
                if curr_ratio > best_ratio:
                    best_fit = name
                    best_ratio = curr_ratio
            pairs.append((best_fit, odds_name))
        return pairs

    pairs_h2h = fuzzy_match_pairs(kalshi_winners_teams, odds_teams_by_market.get("h2h", pd.Index([])))
    pairs_spreads = fuzzy_match_pairs(kalshi_spreads_teams, odds_teams_by_market.get("spreads", pd.Index([])))

    matched_kalshi_h2h = [k for k, _ in pairs_h2h]
    matched_odds_h2h = [o for _, o in pairs_h2h]
    matched_kalshi_spreads = [k for k, _ in pairs_spreads]
    matched_odds_spreads = [o for _, o in pairs_spreads]

    odds_winners_df = odds_winners_df[
        odds_winners_df["home_team"].isin(matched_odds_h2h) | odds_winners_df["away_team"].isin(matched_odds_h2h)
    ].drop_duplicates(subset="team").sort_values(by="home_team").reset_index(drop=True)

    kalshi_winners_df = kalshi_winners_df[
        kalshi_winners_df["home_team"].isin(matched_kalshi_h2h) | kalshi_winners_df["away_team"].isin(matched_kalshi_h2h)
    ].sort_values(by="home_team").reset_index(drop=True)

    odds_spreads_df = odds_spreads_df[odds_spreads_df["team"].isin(matched_odds_spreads)].reset_index(drop=True)
    kalshi_spreads_df = (
        kalshi_spreads_df[kalshi_spreads_df["team"].isin(matched_kalshi_spreads)].reset_index(drop=True)
        if not kalshi_spreads_df.empty
        else kalshi_spreads_df
    )

    kalshi_cols = ["ticker", "yes_bid", "yes_ask", "home_team", "away_team"]
    odds_cols = ["market", "start_time", "team", "home_team", "away_team", "avg_fair_prb"]

    kalshi_subset = kalshi_winners_df[kalshi_cols].rename(columns={"home_team": "kalshi_home_team", "away_team": "kalshi_away_team"})
    odds_subset = odds_winners_df[odds_cols].rename(columns={"home_team": "odds_home_team", "away_team": "odds_away_team"})

    combined_rows = []
    len_matched = min(len(matched_odds_h2h), len(matched_kalshi_h2h))
    for i in range(len_matched):
        odds_name = matched_odds_h2h[i]
        kalshi_name = matched_kalshi_h2h[i]
        odds_row = odds_subset.loc[odds_subset["team"] == odds_name]
        if odds_row.empty:
            continue
        kalshi_rows = kalshi_subset.loc[
            (kalshi_subset["kalshi_home_team"] == kalshi_name) | (kalshi_subset["kalshi_away_team"] == kalshi_name)
        ]
        if kalshi_rows.empty:
            continue
        k1 = kalshi_rows.iloc[0]
        k2 = kalshi_rows.iloc[1] if len(kalshi_rows) > 1 else kalshi_rows.iloc[0]
        midprice1 = (k1["yes_bid"] + k1["yes_ask"]) / 2
        midprice2 = (k2["yes_bid"] + k2["yes_ask"]) / 2
        prb = odds_row["avg_fair_prb"].astype(float).item()
        combined_row = pd.concat([k1, odds_row.iloc[0]])
        if pd.notna(prb):
            if ((midprice1 - prb) ** 2) >= ((midprice2 - prb) ** 2):
                combined_row = pd.concat([k2, odds_row.iloc[0]])
        combined_rows.append(combined_row)

    combined_winners_df = pd.DataFrame(combined_rows).reset_index(drop=True)

    EDGE = CONFIG.DATA.EDGE_WINNERS
    KELLY_UPPERBOUND = CONFIG.DATA.KELLY_CAP
    BANKROLL = bankroll_winners
    Q1_WEIGHT = CONFIG.DATA.Q1_WEIGHT
    Q2_WEIGHT = CONFIG.DATA.Q2_WEIGHT
    Q3_WEIGHT = CONFIG.DATA.Q3_WEIGHT
    Q4_WEIGHT = CONFIG.DATA.Q4_WEIGHT

    edge_winners_df = combined_winners_df.loc[
        (combined_winners_df["avg_fair_prb"] >= combined_winners_df["yes_ask"] + EDGE)
        | (combined_winners_df["avg_fair_prb"] <= combined_winners_df["yes_bid"] - EDGE)
    ].reset_index(drop=True)

    if not edge_winners_df.empty:
        midprice = (edge_winners_df["yes_bid"] + edge_winners_df["yes_ask"]) / 2
        q = edge_winners_df["avg_fair_prb"]
        p = midprice

        edge_winners_df["raw_kelly"] = np.where(q > p, (q - p) / (1 - p), (p - q) / p)

        total_kelly = edge_winners_df["raw_kelly"].sum()
        if total_kelly:
            edge_winners_df["raw_kelly"] = pd.DataFrame(
                {"original": edge_winners_df["raw_kelly"], "normalized": (edge_winners_df["raw_kelly"] / total_kelly)}
            ).min(axis=1)

        def scale_kelly(row):
            k = row["raw_kelly"]
            p_val = row["avg_fair_prb"]
            if k == 0 or pd.isna(k):
                return 0
            if 0.05 <= p_val < 0.25:
                return min(Q1_WEIGHT * k, KELLY_UPPERBOUND)
            elif 0.25 <= p_val < 0.5:
                return min(Q2_WEIGHT * k, KELLY_UPPERBOUND)
            elif 0.5 <= p_val < 0.75:
                return min(Q3_WEIGHT * k, KELLY_UPPERBOUND)
            elif 0.75 <= p_val < 0.95:
                return min(Q4_WEIGHT * k, KELLY_UPPERBOUND)
            else:
                return 0

        edge_winners_df["real_kelly"] = edge_winners_df.apply(scale_kelly, axis=1)
        edge_winners_df["optimal_bet"] = edge_winners_df["real_kelly"] * BANKROLL
        num_contracts = edge_winners_df["optimal_bet"] // edge_winners_df["yes_bid"]
        edge_winners_df["num_contracts"] = num_contracts
        trading_cost = np.ceil(100 * (0.0175 * num_contracts * edge_winners_df["yes_bid"] * (1 - edge_winners_df["yes_bid"]))) / 100
        edge_winners_df["trading_cost"] = trading_cost
        profit = (1 - edge_winners_df["yes_bid"]) * num_contracts - trading_cost
        edge_winners_df["profit"] = profit
        edge_winners_df["ev"] = (profit * edge_winners_df["avg_fair_prb"] - edge_winners_df["optimal_bet"] * (1 - edge_winners_df["avg_fair_prb"])).round(2)
        filtered_winners_df = edge_winners_df.loc[edge_winners_df["ev"] > CONFIG.DATA.WINNERS_EV_THRESHOLD].reset_index(drop=True)
    else:
        filtered_winners_df = pd.DataFrame()

    filtered_spreads_df = pd.DataFrame()
    if not kalshi_spreads_df.empty and not odds_spreads_df.empty:
        kalshi_cols = ["ticker", "yes_bid", "yes_ask", "team", "points"]
        odds_cols = ["market", "start_time", "team", "home_team", "away_team", "avg_fair_prb", "point"]
        odds_subset = odds_spreads_df[odds_cols].rename(columns={"home_team": "odds_home_team", "away_team": "odds_away_team", "team": "odds_team"})
        kalshi_subset = kalshi_spreads_df[kalshi_cols]
        combined_rows = []
        for _, kalshi_row in kalshi_subset.iterrows():
            kalshi_home = kalshi_row["team"]
            for _, odds_row in odds_subset.iterrows():
                odds_home = odds_row["odds_team"]
                if isinstance(kalshi_home, str) and isinstance(odds_home, str) and (kalshi_home in odds_home) and (kalshi_row["points"] == odds_row["point"]):
                    combined_row = pd.concat([kalshi_row, odds_row])
                    combined_rows.append(combined_row)
        combined_spreads_df = pd.DataFrame(combined_rows).drop_duplicates(subset="ticker").reset_index(drop=True)

        EDGE = CONFIG.DATA.EDGE_SPREADS
        BANKROLL = bankroll_spreads

        edge_spreads_df = combined_spreads_df.loc[
            (combined_spreads_df["avg_fair_prb"] >= combined_spreads_df["yes_ask"] + EDGE)
            | (combined_spreads_df["avg_fair_prb"] <= combined_spreads_df["yes_bid"] - EDGE)
        ].reset_index(drop=True)

        if not edge_spreads_df.empty:
            midprice = (edge_spreads_df["yes_bid"] + edge_spreads_df["yes_ask"]) / 2
            q = edge_spreads_df["avg_fair_prb"]
            p = midprice
            edge_spreads_df["raw_kelly"] = np.where(q > p, (q - p) / (1 - p), (p - q) / p)
            total_kelly = edge_spreads_df["raw_kelly"].sum()
            if total_kelly:
                edge_spreads_df["raw_kelly"] = pd.DataFrame(
                    {"original": edge_spreads_df["raw_kelly"], "normalized": (edge_spreads_df["raw_kelly"] / total_kelly)}
                ).min(axis=1)

            def scale_kelly_spreads(row):
                k = row["raw_kelly"]
                p_val = row["avg_fair_prb"]
                if k == 0 or pd.isna(k):
                    return 0
                if 0.1 <= p_val < 0.25:
                    return min(Q1_WEIGHT * k, KELLY_UPPERBOUND)
                elif 0.25 <= p_val < 0.5:
                    return min(Q2_WEIGHT * k, KELLY_UPPERBOUND)
                elif 0.5 <= p_val < 0.75:
                    return min(Q3_WEIGHT * k, KELLY_UPPERBOUND)
                elif 0.75 <= p_val < 0.9:
                    return min(Q4_WEIGHT * k, KELLY_UPPERBOUND)
                else:
                    return 0

            edge_spreads_df["real_kelly"] = edge_spreads_df.apply(scale_kelly_spreads, axis=1)
            edge_spreads_df["optimal_bet"] = edge_spreads_df["real_kelly"] * BANKROLL
            num_contracts = edge_spreads_df["optimal_bet"] // edge_spreads_df["yes_bid"]
            edge_spreads_df["num_contracts"] = num_contracts
            trading_cost = (
                np.ceil(100 * (0.0175 * num_contracts * edge_spreads_df["yes_bid"] * (1 - edge_spreads_df["yes_bid"]))) / 100
            )
            edge_spreads_df["trading_cost"] = trading_cost
            profit = (1 - edge_spreads_df["yes_bid"]) * num_contracts - trading_cost
            edge_spreads_df["profit"] = profit
            edge_spreads_df["ev"] = (profit * edge_spreads_df["avg_fair_prb"] - edge_spreads_df["optimal_bet"] * (1 - edge_spreads_df["avg_fair_prb"])).round(2)
            filtered_spreads_df = edge_spreads_df.loc[edge_spreads_df["ev"] > CONFIG.DATA.SPREADS_EV_THRESHOLD].reset_index(drop=True)

    # Drop events that have already started using odds start_time, after all filtering.
    def _drop_started(df: pd.DataFrame) -> pd.DataFrame:
        if df is None or df.empty or "start_time" not in df.columns:
            return df
        start_clean = df["start_time"].astype(str)
        def _clean_ts(s: str):
            s = s.strip()
            if s.endswith(" CST"):
                return s[:-4] + " -06:00"
            if s.endswith(" CDT"):
                return s[:-4] + " -05:00"
            if s.endswith(" EST"):
                return s[:-4] + " -05:00"
            if s.endswith(" EDT"):
                return s[:-4] + " -04:00"
            if s.endswith(" PST"):
                return s[:-4] + " -08:00"
            if s.endswith(" PDT"):
                return s[:-4] + " -07:00"
            return s
        start_clean = start_clean.apply(_clean_ts)
        ts = pd.to_datetime(start_clean, utc=True, errors="coerce")
        start_float = ts.view("int64") / 1e9
        now_float = pd.Timestamp.utcnow().timestamp()
        print(start_float)
        print(now_float)
        mask = ts.isna() | (start_float > now_float)
        print(mask)
        return df.loc[mask].reset_index(drop=True)

    filtered_winners_df = _drop_started(filtered_winners_df)
    filtered_spreads_df = _drop_started(filtered_spreads_df)

    return filtered_winners_df, filtered_spreads_df


def write_outputs(date_str: str, winners_df: pd.DataFrame, spreads_df: pd.DataFrame) -> Dict[str, str]:
    out_dir = REPO_ROOT / CONFIG.DATA.OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {}
    winners_path = out_dir / f"filtered_winners_{date_str}.csv"
    spreads_path = out_dir / f"filtered_spreads_{date_str}.csv"
    if winners_df is not None:
        winners_df.to_csv(winners_path, index=False)
        paths["winners"] = str(winners_path)
    if spreads_df is not None:
        spreads_df.to_csv(spreads_path, index=False)
        paths["spreads"] = str(spreads_path)
    return paths


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
    price = row.get("yes_bid")
    contracts = row.get("num_contracts")
    if ticker is None or contracts is None or price is None:
        return
    try:
        count = int(contracts)
    except Exception:
        return
    if count <= 0:
        return

    side = "yes"
    action = "buy" if direction == "buy_yes" else "sell"
    price = clamp_price(float(price))
    if price is None:
        return

    payload = {
        "ticker": ticker,
        "side": side,
        "action": action,
        "type": "limit",
        "count": count,
        "time_in_force": "good_til_cancel",
        "client_order_id": str(uuid.uuid4()),
    }
    price_cents = int(round(price * 100))
    payload["yes_price"] = price_cents

    resp = place_order(payload, idem_key(ticker))
    log_order("place", payload, resp)
    mode = "LIVE" if not CONFIG.DRY_RUN else "DRY-RUN"
    if resp and (resp.get("dry_run") or resp.get("ok") or resp.get("order")):
        print(f"🪙 {mode} order: {ticker} side={side} action={action} size={count} price={price:.2f}")
    else:
        print(f"❌ order failed for {ticker} side={side} size={count} price={price}")


def process_dataframe(df: pd.DataFrame, edge: float, source: str):
    for _, row in df.iterrows():
        fair = row.get("avg_fair_prb")
        yes_bid = row.get("yes_bid")
        yes_ask = row.get("yes_ask")
        if fair is None or yes_bid is None or yes_ask is None:
            continue
        direction = None
        if fair >= yes_ask + edge:
            direction = "buy_yes"
        elif fair <= yes_bid - edge:
            direction = "sell_yes"
        if direction:
            submit_order(row, direction)


# =========================
# ========= MAIN ==========
# =========================


def parse_cli_args():
    parser = argparse.ArgumentParser(description="Kalshi live trader v2 (dataframe-driven)")
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

    if not CONFIG.API_KEY or CONFIG.API_KEY == "KALSHI_API_KEY":
        sys.exit("❌ Please set a valid API key via CONFIG or --api-key/KALSHI_API_KEY.")

    select_working_host(args.host or env_host)

    total_bankroll = CONFIG.DATA.TOTAL_BANKROLL or fetch_total_bankroll()
    if total_bankroll is None:
        sys.exit("❌ Could not fetch total bankroll from account and no override provided.")
    winners_bankroll = total_bankroll * CONFIG.DATA.WINNERS_PROPORTION
    spreads_bankroll = total_bankroll * CONFIG.DATA.SPREADS_PROPORTION

    date_str = CONFIG.DATA.DATE or now_utc().date().isoformat()

    winners_df, spreads_df = build_filtered_frames(date_str, winners_bankroll, spreads_bankroll)
    paths = write_outputs(date_str, winners_df, spreads_df)
    for label, path in paths.items():
        print(f"🗒️ {label} CSV written to: {path}")

    # Place orders based on dataframes.
    process_dataframe(winners_df, CONFIG.DATA.EDGE_WINNERS, "winners")
    process_dataframe(spreads_df, CONFIG.DATA.EDGE_SPREADS, "spreads")

    print("✅ Completed processing; exiting.")


if __name__ == "__main__":
    main()
