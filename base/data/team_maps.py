"""
Team mapping dictionaries for converting team names to Kalshi ticker codes.
These mappings are used to construct Kalshi event tickers from team names.
"""

# Try importing from various locations
try:
    # Try from myles_repo structure
    from sourcing.team_map import TEAM_MAP as _TEAM_MAP
    from sourcing.team_map_nba import NBA_TEAM_MAP as _NBA_TEAM_MAP
    TEAM_MAP = _TEAM_MAP
    NBA_TEAM_MAP = _NBA_TEAM_MAP
except ImportError:
    try:
        # Try from copied files in data directory (relative import)
        import sys
        from pathlib import Path
        data_dir = Path(__file__).parent
        if (data_dir / "team_map_full.py").exists():
            # Import directly from file
            import importlib.util
            spec = importlib.util.spec_from_file_location("team_map_full", data_dir / "team_map_full.py")
            team_map_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(team_map_module)
            _TEAM_MAP = team_map_module.TEAM_MAP
        else:
            raise ImportError
        
        if (data_dir / "team_map_nba.py").exists():
            spec_nba = importlib.util.spec_from_file_location("team_map_nba", data_dir / "team_map_nba.py")
            nba_module = importlib.util.module_from_spec(spec_nba)
            spec_nba.loader.exec_module(nba_module)
            _NBA_TEAM_MAP = nba_module.NBA_TEAM_MAP
        else:
            raise ImportError
        
        TEAM_MAP = _TEAM_MAP
        NBA_TEAM_MAP = _NBA_TEAM_MAP
    except (ImportError, AttributeError, Exception):
        # Fallback: copy essential mappings here
        # Full mappings are too large - using a subset for core teams
        TEAM_MAP = {
        # Common NCAA teams - add more as needed
        "kansas": "KU", "university of kansas": "KU", "ku": "KU",
        "kentucky": "UK", "university of kentucky": "UK", "uk": "UK",
        "duke": "DUKE", "duke university": "DUKE",
        "north carolina": "UNC", "university of north carolina": "UNC", "unc": "UNC",
        "michigan": "MICH", "university of michigan": "MICH", "mich": "MICH",
        "michigan state": "MSU", "michigan st": "MSU", "msu": "MSU",
        "ohio state": "OSU", "ohio st": "OSU", "osu": "OSU",
        "texas": "TEX", "university of texas": "TEX",
        "florida": "FLA", "university of florida": "FLA", "uf": "FLA",
        "alabama": "ALA", "university of alabama": "ALA",
        "ucla": "UCLA", "university of california los angeles": "UCLA",
        "usc": "USC", "university of southern california": "USC",
        "virginia": "UVA", "university of virginia": "UVA", "uva": "UVA",
        "indiana": "IND", "indiana university": "IND", "ind": "IND",
        "iowa": "IOWA", "university of iowa": "IOWA",
        "wisconsin": "WIS", "university of wisconsin": "WIS",
        "purdue": "PUR", "purdue university": "PUR",
        "maryland": "MD", "university of maryland": "MD",
        "illinois": "ILL", "university of illinois": "ILL",
        "north carolina state": "NCST", "nc state": "NCST",
        "syracuse": "SYR", "syracuse university": "SYR",
        "louisville": "LOU", "louisville university": "LOU",
        "notre dame": "ND", "notre dame university": "ND",
        "gonzaga": "GONZ", "gonzaga university": "GONZ",
        "villanova": "VILL", "villanova university": "VILL", "nova": "VILL",
        "connecticut": "CONN", "uconn": "CONN",
        "baylor": "BAY", "baylor university": "BAY",
        "houston": "HOU", "university of houston": "HOU",
        "tennessee": "TENN", "university of tennessee": "TENN",
        "arkansas": "ARK", "university of arkansas": "ARK",
        "auburn": "AUB", "university of auburn": "AUB",
        "oklahoma": "OKLA", "university of oklahoma": "OKLA",
        "kansas state": "KSU", "kansas st": "KSU",
        "texas tech": "TTU", "texas tech university": "TTU",
        "iowa state": "ISU", "iowa st": "ISU",
        "west virginia": "WVU", "west virginia university": "WVU",
        "texas am": "TXAM", "texas a&m": "TXAM",
        "lsu": "LSU", "louisiana state university": "LSU",
        "ole miss": "MISS", "mississippi": "MISS",
        "mississippi state": "MSST", "miss st": "MSST",
        "georgia": "UGA", "university of georgia": "UGA",
        "florida state": "FSU", "florida st": "FSU",
        "clemson": "CLEM", "clemson university": "CLEM",
        "miami": "MIA", "miami fl": "MIA",
        "georgia tech": "GT", "georgia tech university": "GT",
        "virginia tech": "VT", "virginia tech university": "VT",
        "wake forest": "WAKE", "wake forest university": "WAKE",
        "boston college": "BC", "bc": "BC",
        "pittsburgh": "PITT", "pitt": "PITT",
        "northwestern": "NW", "northwestern university": "NW",
        "minnesota": "MINN", "university of minnesota": "MINN",
        "nebraska": "NEB", "university of nebraska": "NEB",
        "penn state": "PSU", "penn st": "PSU",
        "rutgers": "RUTG", "rutgers university": "RUTG",
        "colorado": "COLO", "university of colorado": "COLO",
        "utah": "UTAH", "university of utah": "UTAH",
        "arizona": "ARIZ", "university of arizona": "ARIZ",
        "arizona state": "ASU", "arizona st": "ASU",
        "oregon": "ORE", "university of oregon": "ORE",
        "oregon state": "ORST", "oregon st": "ORST",
        "washington": "UW", "university of washington": "UW",
        "washington state": "WSU", "washington st": "WSU",
        "stanford": "STAN", "stanford university": "STAN",
        "california": "CAL", "university of california": "CAL",
        }
        
        NBA_TEAM_MAP = {
            "atlanta hawks": "atl", "hawks": "atl", "atlanta": "atl", "atl": "atl",
            "boston celtics": "bos", "celtics": "bos", "boston": "bos", "bos": "bos",
            "brooklyn nets": "bkn", "nets": "bkn", "brooklyn": "bkn", "bkn": "bkn",
            "charlotte hornets": "cha", "hornets": "cha", "charlotte": "cha", "cha": "cha",
            "chicago bulls": "chi", "bulls": "chi", "chicago": "chi", "chi": "chi",
            "cleveland cavaliers": "cle", "cavaliers": "cle", "cleveland": "cle", "cavs": "cle", "cle": "cle",
            "dallas mavericks": "dal", "mavericks": "dal", "dallas": "dal", "mavs": "dal", "dal": "dal",
            "denver nuggets": "den", "nuggets": "den", "denver": "den", "den": "den",
            "detroit pistons": "det", "pistons": "det", "detroit": "det", "det": "det",
            "golden state warriors": "gsw", "warriors": "gsw", "golden state": "gsw", "gsw": "gsw",
            "houston rockets": "hou", "rockets": "hou", "houston": "hou", "hou": "hou",
            "indiana pacers": "ind", "pacers": "ind", "indiana": "ind", "ind": "ind",
            "la clippers": "lac", "los angeles clippers": "lac", "clippers": "lac", "lac": "lac",
            "los angeles lakers": "lal", "lakers": "lal", "la lakers": "lal", "lal": "lal",
            "memphis grizzlies": "mem", "grizzlies": "mem", "memphis": "mem", "mem": "mem",
            "miami heat": "mia", "heat": "mia", "miami": "mia", "mia": "mia",
            "milwaukee bucks": "mil", "bucks": "mil", "milwaukee": "mil", "mil": "mil",
            "minnesota timberwolves": "min", "timberwolves": "min", "wolves": "min", "minnesota": "min", "min": "min",
            "new orleans pelicans": "no", "pelicans": "no", "new orleans": "no", "no": "no",
            "new york knicks": "nyk", "knicks": "nyk", "ny knicks": "nyk", "nyk": "nyk",
            "oklahoma city thunder": "okc", "thunder": "okc", "oklahoma city": "okc", "okc": "okc",
            "orlando magic": "orl", "magic": "orl", "orlando": "orl", "orl": "orl",
            "philadelphia 76ers": "phi", "76ers": "phi", "sixers": "phi", "philadelphia": "phi", "phi": "phi",
            "phoenix suns": "phx", "suns": "phx", "phoenix": "phx", "phx": "phx",
            "portland trail blazers": "por", "trail blazers": "por", "blazers": "por", "portland": "por", "por": "por",
            "sacramento kings": "sac", "kings": "sac", "sacramento": "sac", "sac": "sac",
            "san antonio spurs": "sas", "spurs": "sas", "san antonio": "sas", "sas": "sas",
            "toronto raptors": "tor", "raptors": "tor", "toronto": "tor", "tor": "tor",
            "utah jazz": "uta", "jazz": "uta", "utah": "uta", "uta": "uta",
            "washington wizards": "was", "wizards": "was", "washington": "was", "was": "was", "wsh": "was",
        }
