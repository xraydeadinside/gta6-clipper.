"""YouTube Data API v3 upload (free quota). Needs a refresh token from scripts/get_youtube_token.py."""
import json
from pathlib import Path

import requests

from .config import env


def _access_token() -> str:
    r = requests.post("https://oauth2.googleapis.com/token", data={
        "client_id": env("YT_CLIENT_ID", required=True),
        "client_secret": env("YT_CLIENT_SECRET", required=True),
        "refresh_token": env("YT_REFRESH_TOKEN", required=True),
        "grant_type": "refresh_token",
    }, timeout=30)
    r.raise_for_status()
    return r.json()["access_token"]


def upload(path: Path, title: str, description: str, tags: list[str], cfg: dict, synthetic: bool = False) -> dict:
    """Returns {"id", "url", "privacy", "locked"}; locked=True means YouTube forced it private (unaudited API)."""
    yt = cfg["youtube"]
    tok = _access_token()
    body = {
        "snippet": {"title": title[:100], "description": description[:4900], "tags": tags[:30],
                    "categoryId": yt["category_id"]},
        "status": {"privacyStatus": yt["privacy"], "selfDeclaredMadeForKids": False,
                   "containsSyntheticMedia": bool(synthetic)},
    }
    init = requests.post(
        "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status",
        headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json; charset=UTF-8",
                 "X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(path.stat().st_size)},
        data=json.dumps(body), timeout=60)
    if init.status_code >= 400 and "containsSyntheticMedia" in init.text:
        body["status"].pop("containsSyntheticMedia", None)  # older API surface — retry without the field
        init = requests.post(
            "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status",
            headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json; charset=UTF-8",
                     "X-Upload-Content-Type": "video/mp4"}, data=json.dumps(body), timeout=60)
    init.raise_for_status()
    with open(path, "rb") as f:
        up = requests.put(init.headers["Location"], data=f, headers={"Content-Type": "video/mp4"}, timeout=900)
    up.raise_for_status()
    vid = up.json()["id"]
    st = requests.get("https://www.googleapis.com/youtube/v3/videos", params={"part": "status", "id": vid},
                      headers={"Authorization": f"Bearer {tok}"}, timeout=30).json()
    privacy = (st.get("items") or [{}])[0].get("status", {}).get("privacyStatus", "unknown")
    return {"id": vid, "url": f"https://youtube.com/shorts/{vid}", "privacy": privacy,
            "locked": yt["privacy"] == "public" and privacy == "private"}
