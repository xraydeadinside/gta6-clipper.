"""Twitch Helix API (free): find fresh clips of whitelisted streamers in the GTA VI category."""
import datetime as dt
import time

import requests

from .config import env

API = "https://api.twitch.tv/helix"
_token = {"value": None, "exp": 0}


def _headers() -> dict:
    if not _token["value"] or time.time() > _token["exp"] - 60:
        r = requests.post(
            "https://id.twitch.tv/oauth2/token",
            data={
                "client_id": env("TWITCH_CLIENT_ID", required=True),
                "client_secret": env("TWITCH_CLIENT_SECRET", required=True),
                "grant_type": "client_credentials",
            },
            timeout=20,
        )
        r.raise_for_status()
        js = r.json()
        _token["value"] = js["access_token"]
        _token["exp"] = time.time() + js.get("expires_in", 3600)
    return {"Client-Id": env("TWITCH_CLIENT_ID"), "Authorization": f"Bearer {_token['value']}"}


def _get(path: str, params) -> dict:
    r = requests.get(f"{API}/{path}", headers=_headers(), params=params, timeout=20)
    r.raise_for_status()
    return r.json()


def game_id(name: str) -> str | None:
    data = _get("games", {"name": name}).get("data", [])
    return data[0]["id"] if data else None


def user_ids(logins: list[str]) -> dict[str, str]:
    out = {}
    for i in range(0, len(logins), 100):
        chunk = logins[i:i + 100]
        for u in _get("users", [("login", l) for l in chunk]).get("data", []):
            out[u["login"].lower()] = u["id"]
    return out


def _iso(t: dt.datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def recent_clips(cfg: dict) -> list[dict]:
    """Return candidate clips sorted by views (desc), already filtered by game and views."""
    d = cfg["discovery"]
    gid = game_id(cfg["game_name"])
    if not gid:
        print(f"[twitch] game not found: {cfg['game_name']}")
        return []
    now = dt.datetime.now(dt.timezone.utc)
    window = {"started_at": _iso(now - dt.timedelta(hours=d["lookback_hours"])), "ended_at": _iso(now), "first": 50}

    logins = [s["login"].lower() for s in cfg.get("streamers", []) if s.get("login")]
    ids = user_ids(logins) if logins else {}
    clips = []
    for login, bid in ids.items():
        clips += _get("clips", {"broadcaster_id": bid, **window}).get("data", [])
    if d.get("whole_category"):
        clips += _get("clips", {"game_id": gid, **window}).get("data", [])

    rev = {v: k for k, v in ids.items()}
    seen_ids, out = set(), []
    for c in clips:
        if c["id"] in seen_ids or c.get("game_id") != gid:
            continue
        if c.get("view_count", 0) < d["min_views"] or c.get("duration", 0) < d["min_seconds"]:
            continue
        seen_ids.add(c["id"])
        out.append({
            "key": f"clip:{c['id']}",
            "kind": "clip",
            "url": c["url"],
            "streamer": c["broadcaster_name"],
            "streamer_login": rev.get(c["broadcaster_id"], c["broadcaster_name"].lower()),
            "title": c.get("title", ""),
            "views": c.get("view_count", 0),
            "duration": c.get("duration", 0),
            "vod_id": c.get("video_id"),
            "vod_offset": c.get("vod_offset"),
        })
    out.sort(key=lambda x: x["views"], reverse=True)
    return out
