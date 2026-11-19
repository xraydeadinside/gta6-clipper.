"""Gemini (free tier) decides: is it funny/viral, where to trim, hook, titles, description, Flow prompt."""
import json
import re
import subprocess
import time
from pathlib import Path

from .config import env
from .transcribe import as_numbered_text

PROMPT = """You are the head editor of a viral YouTube Shorts channel about GTA 6 (Grand Theft Auto VI).
Channel voice: {voice}

You get one moment from a Twitch stream by {streamer} (clip title from viewers: "{clip_title}", {views} views).
{video_note}
Transcript with word indices (index:word), times are seconds from clip start:
{transcript}

Clip length: {duration:.1f}s. Viewers today have a 1-2 second attention span: the short must
start AT the action/punchline setup (no slow intro) and end right after the payoff/reaction.

Return ONLY JSON with these keys:
- "score": 0-10, how likely this is to perform as a Short (funny, shocking, skillful, chaotic, relatable). Be strict.
- "category": one of "funny","fail","insane","rage","wholesome","glitch","skill","story"
- "reason_ru": 1 short sentence IN RUSSIAN explaining why it is (not) good — this is for the channel owner.
- "trim_start": seconds where the short should start (>=0)
- "trim_end": seconds where it should end (<= clip length, total <= 58s)
- "hook_text": 2-5 word SCREAMING on-screen headline shown in the first 2.5s, creates curiosity, no spoilers of the punchline (e.g. "HE DID NOT EXPECT THIS", "WORST DRIVER IN VICE CITY")
- "callouts": up to 3 objects {{"t": seconds, "text": "2-3 WORD SHOUT"}} — huge pop-up texts at the craziest beats (e.g. "NO WAY", "HE'S COOKED", "SEND HELP"). Letters only, no emoji.
- "facecam_box": if the streamer's webcam overlay is visible, its box as [x, y, width, height] in FRACTIONS of the frame (0-1, top-left origin), else null
- "focus": up to 10 objects {{"t": seconds, "x": 0-1, "y": 0-1}} — where the important action is on screen at that time (car, character, explosion), so a virtual camera can zoom/pan there. Ignore the webcam overlay.
- "reactions": up to 4 timestamps (seconds) of the streamer's strongest facial/voice reactions (laughing, screaming, shock) worth a quick webcam close-up
- "titles": 3 different title options (<=60 chars each), English, different angles (curiosity / reaction / stakes). Each should make people stop scrolling. Optional single emoji. Don't put #shorts in titles.
- "description": 2-3 lines English description with a question to drive comments; credit the streamer as twitch.tv/{login}
- "hashtags": 5-8 hashtags without '#', lowercase
- "emphasis": list of up to 6 word indices (from the transcript) that are punchlines / key words to punch-in zoom on
- "edit_story_ru": 2-3 simple sentences IN RUSSIAN for the channel owner: what the moment is and how you edit it to keep attention (e.g. "Сразу начинаем с погони, на падении резкий наезд, потом крупно лицо стримера, который орёт.")
- "edit_plan": the edit as beats covering trim_start..trim_end in order, think like a pro Shorts editor:
  every 1-3 seconds something must change (cut, zoom, pan, face, text). Each beat:
  {{"start": s, "end": s, "role": "hook|setup|build|punchline|reaction|payoff|filler",
    "shot": "game|cam" (cam = full-screen streamer webcam close-up, only if a webcam is visible, best for reactions, max 2s),
    "camera": "push|pan|whip|hold" (push = fast smooth zoom-in, pan = smooth move to the action, whip = very fast move, hold = slow drift),
    "zoom": 1.0-2.0 (how close on the action), "x": 0-1, "y": 0-1 (where the action is),
    "why_ru": "very short reason in Russian"}}
  Beats with role "filler" (boring, dead time) will be CUT OUT — use it for anything that kills retention.
- "flow_prompt": an English prompt for Google Flow (Veo) to generate a 3-4 second vertical 9:16 cinematic intro shot that teases this moment in GTA-6-like Vice City/Leonida style, without real people, logos or copyrighted characters.
"""

RETITLE = """You write YouTube Shorts titles for a GTA 6 clips channel. Voice: {voice}
Moment: {summary}
Previous titles (do NOT repeat): {old}
Return ONLY JSON: {{"titles": [3 new title options, <=60 chars, English, different angles]}}"""

REFLOW = """Write a new English prompt for Google Flow (Veo) for a 3-4 second vertical 9:16 cinematic intro shot
teasing this GTA 6 stream moment: {summary}. Previous prompt (make it clearly different): {old}.
Owner's wishes (may be empty, may be in Russian): {wish}
No real people, no logos, no copyrighted characters. Return ONLY JSON: {{"flow_prompt": "..."}}"""


def _client():
    from google import genai
    return genai.Client(api_key=env("GEMINI_API_KEY", required=True))


def _parse_json(text: str) -> dict:
    text = text.strip()
    m = re.search(r"\{.*\}", text, re.S)
    return json.loads(m.group(0) if m else text)


def _generate(models: list[str], contents, temperature: float = 0.9) -> dict:
    from google.genai import types
    client = _client()
    last = None
    for model in models:
        for attempt in range(3):
            try:
                r = client.models.generate_content(
                    model=model, contents=contents,
                    config=types.GenerateContentConfig(response_mime_type="application/json", temperature=temperature),
                )
                return _parse_json(r.text)
            except Exception as e:  # 429 / 503 / bad json -> retry, then next model
                last = e
                msg = str(e)
                if "429" in msg or "RESOURCE_EXHAUSTED" in msg:
                    time.sleep(20 * (attempt + 1))
                    if attempt == 1:
                        break  # daily quota likely gone on this model -> next model
                elif "404" in msg or "NOT_FOUND" in msg:
                    break
                else:
                    time.sleep(5)
    raise RuntimeError(f"Gemini failed on all models: {last}")


def _upload_proxy(src: Path):
    """Upload a small 360p proxy so Gemini can watch the clip (saves free-tier tokens)."""
    proxy = src.with_name(src.stem + "_proxy.mp4")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-vf", "scale=-2:360", "-r", "15",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "30", "-c:a", "aac", "-b:a", "64k", str(proxy)],
                   check=True)
    client = _client()
    f = client.files.upload(file=str(proxy))
    for _ in range(60):
        f = client.files.get(name=f.name)
        state = str(getattr(f, "state", "")).upper()
        if "ACTIVE" in state:
            return f
        if "FAILED" in state:
            raise RuntimeError("Gemini file processing failed")
        time.sleep(2)
    raise RuntimeError("Gemini file processing timeout")


def judge(cfg: dict, item: dict, words: list[dict], src: Path, duration: float) -> dict:
    b = cfg["brain"]
    contents = []
    video_note = "Only the transcript is available."
    if b.get("send_video", True):
        try:
            contents.append(_upload_proxy(src))
            video_note = "The video of the moment is attached: WATCH it, visual comedy and gameplay chaos matter as much as speech."
        except Exception as e:
            print(f"[brain] video upload skipped: {e}")
    contents.append(PROMPT.format(
        voice=b["channel_voice"], streamer=item["streamer"], login=item["streamer_login"],
        clip_title=item.get("title", ""), views=item.get("views", 0), video_note=video_note,
        transcript=as_numbered_text(words), duration=duration,
    ))
    d = _generate(b["models"], contents)
    return normalize(d, duration, len(words))


def normalize(d: dict, duration: float, n_words: int) -> dict:
    """Clamp/clean whatever the model returned so the editor never crashes."""
    out = {
        "score": float(d.get("score", 0) or 0),
        "category": str(d.get("category", "funny")),
        "reason_ru": str(d.get("reason_ru", "")),
        "hook_text": str(d.get("hook_text", "")).upper()[:40],
        "titles": [str(t)[:90] for t in (d.get("titles") or []) if str(t).strip()][:3] or ["GTA 6 moment you have to see"],
        "description": str(d.get("description", "")),
        "hashtags": [str(h).lstrip("#").replace(" ", "").lower() for h in (d.get("hashtags") or [])][:8],
        "emphasis": [],
        "flow_prompt": str(d.get("flow_prompt", "")),
    }
    try:
        ts = max(0.0, float(d.get("trim_start", 0)))
        te = min(duration, float(d.get("trim_end", duration)))
    except (TypeError, ValueError):
        ts, te = 0.0, duration
    if te - ts < 3:
        ts, te = 0.0, duration
    out["trim_start"], out["trim_end"] = ts, te
    out["callouts"] = []
    for c in (d.get("callouts") or [])[:3]:
        try:
            if ts <= float(c["t"]) <= te and str(c.get("text", "")).strip():
                out["callouts"].append({"t": float(c["t"]), "text": str(c["text"])[:24]})
        except (TypeError, ValueError, KeyError):
            pass
    out["focus"] = []
    for f in (d.get("focus") or [])[:12]:
        try:
            x, y, t = float(f["x"]), float(f["y"]), float(f["t"])
            if 0 <= x <= 1 and 0 <= y <= 1:
                out["focus"].append({"t": t, "x": min(0.85, max(0.15, x)), "y": min(0.8, max(0.2, y))})
        except (TypeError, ValueError, KeyError):
            pass
    out["reactions"] = []
    for r in (d.get("reactions") or [])[:4]:
        try:
            out["reactions"].append(float(r))
        except (TypeError, ValueError):
            pass
    out["facecam_box"] = None
    fb = d.get("facecam_box")
    try:
        if fb and len(fb) == 4:
            x, y, w, h = [float(v) for v in fb]
            if max(x, y, w, h) > 1.5:          # model answered in pixels of 1920x1080
                x, y, w, h = x / 1920, y / 1080, w / 1920, h / 1080
            # plausible webcam: 6-60% of width, inside frame
            if 0.06 <= w <= 0.6 and 0.08 <= h <= 0.7 and x >= 0 and y >= 0 and x + w <= 1.02 and y + h <= 1.02:
                out["facecam_box"] = [int(x * 1920), int(y * 1080), int(w * 1920), int(h * 1080)]
    except (TypeError, ValueError):
        pass
    out["edit_story_ru"] = str(d.get("edit_story_ru", ""))[:400]
    out["edit_plan"] = normalize_beats(d.get("edit_plan"), ts, te)
    for i in d.get("emphasis") or []:
        try:
            i = int(i)
            if 0 <= i < n_words:
                out["emphasis"].append(i)
        except (TypeError, ValueError):
            pass
    return out


REPLAN = """You are the head editor of a GTA 6 Shorts channel. Re-edit this moment following the owner's wish.
Owner's wish (Russian or English): {wish}
Clip length {duration:.1f}s. Streamer webcam visible: {has_cam}.
Transcript (index:word, seconds from clip start):
{transcript}
Current edit (JSON): {current}
Return ONLY JSON with keys "trim_start", "trim_end", "edit_story_ru", "edit_plan" (same beat format as current:
start,end,role,shot,camera,zoom,x,y,why_ru; role "filler" = cut out), "hook_text", "callouts" ([{{"t","text"}}]).
Change what the owner asked for, keep what works."""


def replan(cfg: dict, item: dict, wish: str, duration: float, has_cam: bool) -> dict:
    from .transcribe import as_numbered_text
    current = {k: item.get(k) for k in ("trim_start", "trim_end", "edit_story_ru", "edit_plan", "hook_text", "callouts")}
    d = _generate(cfg["brain"]["models"], REPLAN.format(
        wish=wish, duration=duration, has_cam=has_cam, transcript=as_numbered_text(item.get("words", [])),
        current=json.dumps(current, ensure_ascii=False)), temperature=0.7)
    merged = {**item, **{k: v for k, v in d.items() if v not in (None, "", [])}}
    n = normalize(merged, duration, len(item.get("words", [])))
    return {k: n[k] for k in ("trim_start", "trim_end", "edit_story_ru", "edit_plan", "hook_text", "callouts")}


def retitle(cfg: dict, item: dict) -> list[str]:
    summary = f"{item['streamer']}: {item.get('reason_ru', '')} / hook: {item.get('hook_text', '')} / clip title: {item.get('title', '')}"
    d = _generate(cfg["brain"]["models"], RETITLE.format(voice=cfg["brain"]["channel_voice"], summary=summary,
                                                         old="; ".join(item.get("titles", []))), temperature=1.1)
    return [str(t)[:90] for t in d.get("titles", [])][:3]


def new_flow_prompt(cfg: dict, item: dict, wish: str = "") -> str:
    summary = f"{item.get('reason_ru', '')} / hook: {item.get('hook_text', '')} / clip title: {item.get('title', '')}"
    d = _generate(cfg["brain"]["models"], REFLOW.format(summary=summary, old=item.get("flow_prompt", ""), wish=wish),
                  temperature=1.0)
    return str(d.get("flow_prompt", ""))


ROLES = {"hook", "setup", "build", "punchline", "reaction", "payoff", "filler"}
CAMERAS = {"push", "pan", "whip", "hold"}


def normalize_beats(beats, ts: float, te: float) -> list[dict]:
    """Clean the model's edit plan: sorted, inside the trim, no overlaps, sane values."""
    out = []
    for b in beats or []:
        try:
            a, e = max(ts, float(b["start"])), min(te, float(b["end"]))
        except (TypeError, ValueError, KeyError):
            continue
        if out:
            a = max(a, out[-1]["end"])
        if e - a < 0.3:
            continue
        role = str(b.get("role", "build")).lower()
        cam = str(b.get("camera", "hold")).lower()
        try:
            zoom = min(2.0, max(1.0, float(b.get("zoom", 1.2))))
            x = min(0.85, max(0.15, float(b.get("x", 0.5))))
            y = min(0.8, max(0.2, float(b.get("y", 0.5))))
        except (TypeError, ValueError):
            zoom, x, y = 1.2, 0.5, 0.5
        out.append({"start": round(a, 3), "end": round(e, 3),
                    "role": role if role in ROLES else "build",
                    "shot": "cam" if str(b.get("shot", "game")).lower() == "cam" else "game",
                    "camera": cam if cam in CAMERAS else "hold",
                    "zoom": zoom, "x": x, "y": y, "why_ru": str(b.get("why_ru", ""))[:120]})
    if not [b for b in out if b["role"] != "filler"]:
        return []
    return out
