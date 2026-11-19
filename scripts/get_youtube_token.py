"""Run ONCE on your laptop to connect your YouTube channel (light script, any laptop is fine).

    pip install google-auth-oauthlib
    python scripts/get_youtube_token.py client_secret.json

A browser opens -> pick the channel's Google account -> allow.
The script prints YT_CLIENT_ID / YT_CLIENT_SECRET / YT_REFRESH_TOKEN -> paste them into GitHub Secrets.
"""
import json
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.readonly"]

path = sys.argv[1] if len(sys.argv) > 1 else "client_secret.json"
flow = InstalledAppFlow.from_client_secrets_file(path, SCOPES)
creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
info = json.load(open(path))
info = info.get("installed") or info.get("web")
print("\n=== Paste into GitHub -> Settings -> Secrets and variables -> Actions ===")
print("YT_CLIENT_ID     =", info["client_id"])
print("YT_CLIENT_SECRET =", info["client_secret"])
print("YT_REFRESH_TOKEN =", creds.refresh_token)
