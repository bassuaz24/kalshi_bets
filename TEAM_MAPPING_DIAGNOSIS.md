# Team Mapping Diagnosis Report

## Summary
**YES, team mapping is the primary issue causing matching problems.**

The diagnostic shows that **12.2% of teams (12 out of 98) cannot be matched** from OddsAPI data to Kalshi team codes.

## Critical Issues Found

### 1. Missing Teams in TEAM_MAP
The following teams are **completely missing** from `team_map_full.py`:

- **Hawai'i Rainbow Warriors** / **Hawaii Rainbow Warriors** / **Hawaii** ⚠️ **CRITICAL**
  - This is the exact team mentioned in your issue (Cal Poly/Hawaii game)
  - No variations found in TEAM_MAP

- **Liberty Flames** / **Liberty**
- **NJIT Highlanders** / **NJIT**
- **IUPUI Jaguars** / **IUPUI**
- **Vermont Catamounts** / **Vermont**
- **Lipscomb Bisons** / **Lipscomb**
- **Towson Tigers** / **Towson**
- **SE Missouri St Redhawks** / **Southeast Missouri State**

### 2. Name Variation Mismatches
Some teams exist in the map but with different name formats that don't match OddsAPI:

- **Charleston Cougars**: OddsAPI uses "Charleston Cougars", but TEAM_MAP has "coll of charleston" → "COFC"
- **Wright St Raiders**: OddsAPI uses "Wright St Raiders", but TEAM_MAP has "wright state" → "WRST"
- **N Colorado Bears**: OddsAPI uses "N Colorado Bears", but TEAM_MAP has "northern colorado" → "UNCO"
- **Sacramento St Hornets**: OddsAPI uses "Sacramento St Hornets", but TEAM_MAP has "sacramento state" → "SAC"

The `smart_team_lookup` function should handle some variations (like removing mascots), but it's not catching all cases.

### 3. NBA Teams Work Correctly
NBA teams like "Dallas Mavericks" and "Utah Jazz" are correctly mapped via `NBA_TEAM_MAP`, so the issue is primarily with college teams.

## Impact on Matching

When a team cannot be matched:
1. The `smart_team_lookup` function returns `(None, "fallback", None)`
2. The matching logic in `_match_kalshi_market_to_odds` fails to find the correct OddsAPI row
3. The Kalshi market gets no OddsAPI data, even if the data exists

## Recommendations

### Immediate Fixes Needed:

1. **Add missing teams to `team_map_full.py`**:
   - Hawaii (all variations)
   - Liberty
   - NJIT
   - IUPUI
   - Vermont
   - Lipscomb
   - Towson
   - Southeast Missouri State

2. **Add more name variations** for existing teams:
   - "charleston cougars" → "COFC" (in addition to "coll of charleston")
   - "wright st raiders" → "WRST" (in addition to "wright state")
   - "n colorado bears" → "UNCO" (in addition to "northern colorado")
   - "sacramento st hornets" → "SAC" (in addition to "sacramento state")

3. **Improve `smart_team_lookup` function** to better handle:
   - Abbreviated state names ("St" → "State")
   - Mascot removal (already partially works)
   - Common abbreviations ("N" → "Northern", "SE" → "Southeast")

### Long-term Improvements:

1. Create a comprehensive mapping of OddsAPI team names to Kalshi codes
2. Add fuzzy matching fallbacks for common variations
3. Log unmatched teams for continuous improvement

## Test Results

```
Total unique teams found: 98
Successfully matched: 86 (87.8%)
Unmatched: 12 (12.2%)
```

The 87.8% match rate is below the threshold needed for reliable matching, confirming that team mapping is a significant issue.
