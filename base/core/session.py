"""
HTTP session management for API requests.
"""

import requests

SESSION = requests.Session()
SESSION.headers.update({
    "Accept": "application/json",
    "Cache-Control": "no-cache, no-store, must-revalidate",
    "Pragma": "no-cache",
    "Expires": "0",
})