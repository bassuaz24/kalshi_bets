#!/usr/bin/env python3
"""
Diagnostic script to check team mapping issues.
Tests whether OddsAPI team names can be matched to Kalshi team codes.
"""

import sys
import csv
from pathlib import Path

# Add base directory to path
_BASE_ROOT = Path(__file__).parent / "base"
if str(_BASE_ROOT) not in sys.path:
    sys.path.insert(0, str(_BASE_ROOT))

from strategy.utils import smart_team_lookup
from data.team_maps import TEAM_MAP, NBA_TEAM_MAP

def test_team_matching():
    """Test team matching from OddsAPI data."""
    print("=" * 80)
    print("TEAM MAPPING DIAGNOSTIC REPORT")
    print("=" * 80)
    print()
    
    # Test known problematic teams
    print("1. Testing known problematic teams:")
    print("-" * 80)
    test_cases = [
        ("Hawai'i Rainbow Warriors", TEAM_MAP),
        ("Hawaii Rainbow Warriors", TEAM_MAP),
        ("Hawaii", TEAM_MAP),
        ("Cal Poly Mustangs", TEAM_MAP),
        ("Cal Poly", TEAM_MAP),
        ("Dallas Mavericks", NBA_TEAM_MAP),
        ("Utah Jazz", NBA_TEAM_MAP),
    ]
    
    for team_name, team_map in test_cases:
        code, confidence, matched_key = smart_team_lookup(team_name, team_map)
        status = "✓" if code else "✗"
        map_name = "TEAM_MAP" if team_map == TEAM_MAP else "NBA_TEAM_MAP"
        print(f"{status} {team_name:35} -> {code or 'NOT FOUND':6} ({confidence:15}) [{map_name}]")
    print()
    
    # Check OddsAPI data
    oddsapi_file = Path("base/data_collection/data_curr/2026-01-15/cbbm_ncaab_h2h.csv")
    if not oddsapi_file.exists():
        print(f"⚠️  OddsAPI file not found: {oddsapi_file}")
        return
    
    print("2. Testing teams from OddsAPI data:")
    print("-" * 80)
    
    unmatched_teams = []
    matched_count = 0
    total_count = 0
    
    with open(oddsapi_file) as f:
        reader = csv.DictReader(f)
        teams_seen = set()
        
        for row in reader:
            for field in ['team', 'home_team', 'away_team']:
                team_name = row.get(field, '').strip()
                if team_name and team_name not in teams_seen:
                    teams_seen.add(team_name)
                    total_count += 1
                    code, confidence, matched_key = smart_team_lookup(team_name, TEAM_MAP)
                    if code:
                        matched_count += 1
                    else:
                        unmatched_teams.append((team_name, confidence))
    
    print(f"Total unique teams found: {total_count}")
    print(f"Successfully matched: {matched_count} ({matched_count/total_count*100:.1f}%)")
    print(f"Unmatched: {len(unmatched_teams)} ({len(unmatched_teams)/total_count*100:.1f}%)")
    print()
    
    if unmatched_teams:
        print("3. Unmatched teams (first 20):")
        print("-" * 80)
        for team_name, confidence in unmatched_teams[:20]:
            print(f"  ✗ {team_name:50} (confidence: {confidence})")
        if len(unmatched_teams) > 20:
            print(f"  ... and {len(unmatched_teams) - 20} more")
        print()
    
    # Check for common variations
    print("4. Checking for common team name variations:")
    print("-" * 80)
    
    # Check if Hawaii exists in any form
    hawaii_variations = [
        "hawaii", "hawai'i", "hawaii rainbow warriors", "hawai'i rainbow warriors",
        "hawaii warriors", "rainbow warriors", "uh", "university of hawaii"
    ]
    found_hawaii = False
    for var in hawaii_variations:
        if var in TEAM_MAP:
            print(f"  ✓ Found: '{var}' -> {TEAM_MAP[var]}")
            found_hawaii = True
    if not found_hawaii:
        print("  ✗ Hawaii not found in TEAM_MAP in any variation")
    print()
    
    # Summary
    print("=" * 80)
    print("SUMMARY")
    print("=" * 80)
    match_rate = matched_count / total_count * 100 if total_count > 0 else 0
    if match_rate < 90:
        print(f"⚠️  WARNING: Only {match_rate:.1f}% of teams matched successfully!")
        print("   This indicates significant team mapping issues.")
    else:
        print(f"✓ Match rate is {match_rate:.1f}% - team mapping looks good.")
    print()

if __name__ == "__main__":
    test_team_matching()
