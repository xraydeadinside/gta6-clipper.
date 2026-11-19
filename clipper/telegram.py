"""Minimal Telegram Bot API client (works the same in the phone app and Telegram Desktop/Web)."""
import json
from pathlib import Path

import requests

from .config import env

API = "https://api.telegram.org"


def _url(method: str) -> str:
    return f"{API}/bot{env('TELEGRAM_BOT_TOKEN', required=True)}/{method}"


def call(method: str, files=None, **params):
    data = {k: (json.dumps(v) if isinstance(v, (dict, list)) else v) for k, v in params.items() if v is not None}
    r = requests.post(_url(method), data=data, files=files, timeout=300)
    js = r.json()
    if not js.get("ok"):
        raise RuntimeError(f"Telegram {method}: {js}")
    return js["result"]


def kb(rows: list[list[tuple[str, str]]]) -> dict:
    return {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in row] for row in rows]}


def send(chat_id, text: str, markup=None, reply_to=None, force_reply=False):
    if force_reply:
        markup = {"force_reply": True, "input_field_placeholder": "Напиши ответ…"}
    return call("sendMessage", chat_id=chat_id, text=text[:4096], parse_mode="HTML",
                reply_markup=markup, reply_to_message_id=reply_to, disable_web_page_preview=True)


def send_video(chat_id, path: Path, caption: str, markup=None):
    with open(path, "rb") as f:
        return call("sendVideo", files={"video": f}, chat_id=chat_id, caption=caption[:1024],
                    parse_mode="HTML", reply_markup=markup, supports_streaming=True)


def send_document(chat_id, path: Path, caption: str = ""):
    with open(path, "rb") as f:
        return call("sendDocument", files={"document": f}, chat_id=chat_id, caption=caption[:1024], parse_mode="HTML")


def edit_caption(chat_id, message_id, caption: str, markup=None):
    try:
        return call("editMessageCaption", chat_id=chat_id, message_id=message_id, caption=caption[:1024],
                    parse_mode="HTML", reply_markup=markup)
    except RuntimeError as e:
        if "message is not modified" not in str(e):
            print(f"[tg] edit caption failed: {e}")


def answer(callback_id: str, text: str = ""):
    try:
        call("answerCallbackQuery", callback_query_id=callback_id, text=text[:200])
    except RuntimeError:
        pass  # too old (we poll every few minutes) — harmless


def updates(offset: int) -> list[dict]:
    return call("getUpdates", offset=offset, timeout=0, allowed_updates=["message", "callback_query"])


def download_file(file_id: str, dest: Path) -> Path:
    info = call("getFile", file_id=file_id)
    url = f"{API}/file/bot{env('TELEGRAM_BOT_TOKEN')}/{info['file_path']}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, timeout=300) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    return dest
