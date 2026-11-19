import hashlib
import json
import time

from .config import ROOT

STATE_PATH = ROOT / "data" / "state.json"

DEFAULT = {"seen": {}, "items": {}, "tg_offset": 0, "awaiting": {}, "last_discovery": 0}


def load() -> dict:
    if STATE_PATH.exists():
        data = json.loads(STATE_PATH.read_text(encoding="utf-8") or "{}")
    else:
        data = {}
    for k, v in DEFAULT.items():
        data.setdefault(k, json.loads(json.dumps(v)))
    return data


def save(state: dict) -> None:
    prune(state)
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def short_id(source_key: str) -> str:
    """Short stable id (Telegram callback_data is limited to 64 bytes)."""
    return hashlib.sha1(source_key.encode()).hexdigest()[:8]


def prune(state: dict, keep_days: int = 10) -> None:
    """Drop finished items and old 'seen' marks so state.json stays small."""
    now = time.time()
    cutoff = now - keep_days * 86400
    for iid in list(state["items"]):
        it = state["items"][iid]
        if it.get("status") in ("published", "rejected", "failed", "skipped", "manual") and it.get("updated", now) < cutoff:
            del state["items"][iid]
    for mid in list(state["awaiting"]):
        if state["awaiting"][mid].get("id") not in state["items"]:
            del state["awaiting"][mid]
    for key in list(state["seen"]):
        if state["seen"][key] < now - 30 * 86400:
            del state["seen"][key]
