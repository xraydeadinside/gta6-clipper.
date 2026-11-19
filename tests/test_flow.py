"""Offline end-to-end test: fake Twitch/Gemini/Telegram/YouTube, real ffmpeg editing.
Run: python tests/test_flow.py"""
import itertools
import os
import random
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
tmp = Path(tempfile.mkdtemp())
os.environ.update(CLIPPER_WORK=str(tmp / "work"), CLIPPER_OUT=str(tmp / "out"),
                  TELEGRAM_BOT_TOKEN="x", TELEGRAM_CHAT_ID="111", TELEGRAM_FLOW_CHAT_ID="222")

from clipper import pipeline, state as st, telegram as tg, brain, twitch, artifacts  # noqa: E402
from clipper import vod_chat, captions  # noqa: E402

# --- isolated state file
st.STATE_PATH = tmp / "state.json"

# --- synthetic source clip
SRC = tmp / "src.mp4"
subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=s=1280x720:r=30:d=20",
                "-f", "lavfi", "-i", "sine=f=300:r=48000:d=20", "-filter_complex",
                "[1:a]volume='if(between(t,5,7),0,1)':eval=frame[a]", "-map", "0:v", "-map", "[a]",
                "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(SRC)], check=True)
INTRO = tmp / "intro.mp4"
subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "mandelbrot=s=720x1280:r=30",
                "-t", "3", "-c:v", "libx264", "-preset", "ultrafast", str(INTRO)], check=True)

vocab = "no way bro he actually did it chat look at this lmao".split()
WORDS, t = [], 0.4
for w in itertools.cycle(vocab):
    if t > 19:
        break
    if 5 <= t < 7:
        t += 0.3
        continue
    WORDS.append({"w": w, "s": round(t, 2), "e": round(t + 0.25, 2)})
    t += 0.34

# --- fakes
sent = []
updates_q = []
msg_id = itertools.count(100)


def fake_call(method, files=None, **p):
    sent.append((method, p))
    if method == "getUpdates":
        out = list(updates_q)
        updates_q.clear()
        return out
    if method == "getFile":
        return {"file_path": "intro.mp4"}
    return {"message_id": next(msg_id)}


tg.call = fake_call
tg.download_file = lambda fid, dest: (dest.parent.mkdir(parents=True, exist_ok=True), shutil.copy(INTRO, dest), dest)[2]
twitch.recent_clips = lambda cfg: [{"key": "clip:abc", "kind": "clip", "url": "https://clips.twitch.tv/abc",
                                    "streamer": "TestStreamer", "streamer_login": "teststreamer",
                                    "title": "LMAOOO", "views": 999, "duration": 20}]
pipeline.download = lambda url, path, *a: (path.parent.mkdir(parents=True, exist_ok=True),
                                           shutil.copy(SRC, path), (path, {}))[2]
pipeline.transcribe = lambda *a, **k: WORDS
brain.judge = lambda cfg, it, words, src, dur: brain.normalize({
    "score": 8.5, "category": "funny", "reason_ru": "Неожиданный прыжок и реакция", "trim_start": 0.5,
    "trim_end": 19.5, "hook_text": "he did not expect this", "titles": ["T1", "T2", "T3"],
    "description": "What would you do?", "hashtags": ["gta6", "funny"], "emphasis": [3, 15, 30],
    "flow_prompt": "Vertical cinematic shot of a sports car flying off a bridge at sunset",
    "facecam_box": [0.75, 0.04, 0.22, 0.22], "focus": [{"t": 4, "x": 0.3, "y": 0.6}, {"t": 12, "x": 0.6, "y": 0.4}],
    "reactions": [9.0], "callouts": [{"t": 11, "text": "NO WAY 💀"}],
    "edit_story_ru": "Начинаем сразу с прыжка, на падении наезд, потом лицо стримера.",
    "edit_plan": [
        {"start": 0.5, "end": 2.0, "role": "filler", "why_ru": "скучно"},
        {"start": 2.0, "end": 4.8, "role": "setup", "shot": "game", "camera": "push", "zoom": 1.3, "x": 0.3, "y": 0.6},
        {"start": 7.0, "end": 9.0, "role": "punchline", "shot": "game", "camera": "whip", "zoom": 1.8, "x": 0.6, "y": 0.4,
         "why_ru": "главный момент"},
        {"start": 9.0, "end": 10.5, "role": "reaction", "shot": "cam", "camera": "push", "zoom": 1.2},
        {"start": 10.5, "end": 18.0, "role": "payoff", "shot": "game", "camera": "pan", "zoom": 1.4, "x": 0.5, "y": 0.5}]
    }, dur, len(words))
REPLANNED = []
brain.replan = lambda cfg, it, wish, dur, has_cam: (REPLANNED.append(wish) or {
    "trim_start": 2.0, "trim_end": 12.0, "edit_story_ru": "Короче и больше лица", "hook_text": "WATCH THIS",
    "callouts": [], "edit_plan": brain.normalize_beats([
        {"start": 2.0, "end": 6.0, "role": "setup", "camera": "pan", "zoom": 1.5, "x": 0.4, "y": 0.5},
        {"start": 7.0, "end": 9.5, "role": "reaction", "shot": "cam"},
        {"start": 9.5, "end": 12.0, "role": "payoff", "camera": "hold", "zoom": 1.2}], 2.0, 12.0)})
brain.retitle = lambda cfg, it: ["N1", "N2", "N3"]
artifacts.fetch = lambda name, fn, d: Path(os.environ["CLIPPER_OUT"]) / fn
published = []
import clipper.youtube as yt  # noqa: E402
yt.upload = lambda path, title, desc, tags, cfg, synthetic=False: (published.append((path, title, desc, synthetic)) or
                                                                   {"id": "vid1", "url": "https://youtube.com/shorts/vid1",
                                                                    "privacy": "public", "locked": False})


def cb(data, mid=1):
    return {"update_id": random.randint(1, 10**9), "callback_query": {"id": "c", "data": data,
                                                                       "message": {"chat": {"id": 111}, "message_id": mid}}}


def msg(text=None, reply_to=None, video=None, chat=111):
    m = {"chat": {"id": chat}, "message_id": next(msg_id)}
    if text:
        m["text"] = text
    if reply_to:
        m["reply_to_message"] = {"message_id": reply_to}
    if video:
        m["video"] = {"file_id": "VID", "file_size": 1000}
    return {"update_id": random.randint(1, 10**9), "message": m}


# ---------- tick 1: discover -> edit -> send for review
pipeline.tick()
s = st.load()
iid = st.short_id("clip:abc")
it = s["items"][iid]
assert it["status"] == "review", it["status"]
assert any(m == "sendVideo" for m, _ in sent)
out = Path(os.environ["CLIPPER_OUT"]) / it["file"]
assert out.exists() and out.stat().st_size > 10000
assert it["facecam_box"] == [1440, 43, 422, 237], it["facecam_box"]
assert it["callouts"] and it["focus"] and it["reactions"] == [9.0]
assert len(it["edit_plan"]) == 5 and it["edit_plan"][0]["role"] == "filler"
reasoning = [p["text"] for m, p in sent if m == "sendMessage" and "Как я смонтировал" in p.get("text", "")]
assert reasoning and "вырезано" in reasoning[0] and "панчлайн" in reasoning[0], reasoning
dur1 = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(out)],
                            capture_output=True, text=True).stdout)
assert dur1 < 16.5, dur1  # filler + silence were cut
print("tick1 ok: rendered by AI plan,", round(dur1, 1), "s, facecam", it["facecam_box"])
print(reasoning[0])

# ---------- re-edit with a wish
updates_q += [cb(f"re:{iid}")]
pipeline.tick()
s_ = st.load()
wish_mid = next(k for k, v in s_["awaiting"].items() if v["field"] == "edit_wish")
updates_q += [msg("больше лица, короче", reply_to=int(wish_mid))]
pipeline.tick()
s_ = st.load()
assert REPLANNED == ["больше лица, короче"] and s_["items"][iid]["status"] == "review"
assert s_["items"][iid]["hook_text"] == "WATCH THIS"
print("re-edit ok")

# ---------- tick 2: choose title 2, new titles, custom title, flow prompt, intro video
updates_q += [cb(f"t:{iid}:1"), cb(f"nt:{iid}"), cb(f"et:{iid}"), cb(f"fl:{iid}")]
pipeline.tick()
s = st.load()
aw = s["awaiting"]
title_mid = next(k for k, v in aw.items() if v["field"] == "title")
intro_mid = next(k for k, v in aw.items() if v["field"] == "intro")
assert s["items"][iid]["titles"] == ["N1", "N2", "N3"]
updates_q += [msg("My custom GTA title", reply_to=int(title_mid)), msg(video=True, reply_to=int(intro_mid), chat=222)]
pipeline.tick()
s = st.load()
it = s["items"][iid]
assert it["custom_title"] == "My custom GTA title"
assert it["intro_file_id"] == "VID" and it["status"] == "review", it["status"]
dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                            str(Path(os.environ["CLIPPER_OUT"]) / it["file"])], capture_output=True, text=True).stdout)
print("tick2-3 ok: re-rendered with intro, duration", dur)

# ---------- tick 4: publish
updates_q += [cb(f"ok:{iid}")]
pipeline.tick()
s = st.load()
assert s["items"][iid]["status"] == "published", s["items"][iid]["status"]
p, title, desc, synth = published[0]
assert title == "My custom GTA title" and "twitch.tv/teststreamer" in desc and "#shorts" in desc and synth
print("tick4 ok: published ->", s["items"][iid]["youtube"]["url"])

# ---------- duplicates are not re-processed
n_before = sum(1 for m, _ in sent if m == "sendVideo")
s["last_discovery"] = 0
st.save(s)
pipeline.tick()
assert sum(1 for m, _ in sent if m == "sendVideo") == n_before
print("dedupe ok")

# ---------- chat spike detection
random.seed(1)
msgs = [(random.uniform(0, 3600), "hi") for _ in range(3000)]
msgs += [(1203 + random.random() * 8, "KEKW LMAO") for _ in range(150)]
msgs += [(2500 + random.random() * 8, "POG no way") for _ in range(120)]
sp = vod_chat.find_spikes(msgs)
assert abs(sp[0]["peak"] - 1205) < 15 and sp[0]["kind"] == "funny", sp[:2]
assert any(abs(x["peak"] - 2505) < 15 for x in sp)
print("spikes ok:", [(int(x["peak"]), x["kind"], x["score"]) for x in sp[:3]])

# ---------- captions never overlap
w_out = [{"w": w["w"], "s": w["s"], "e": w["e"], "emph": False} for w in WORDS]
ass = captions.build_ass(w_out, 20, hook="x")
prev_end = 0
for line in ass.splitlines():
    if line.startswith("Dialogue: 0,"):
        a, b = line.split(",")[1:3]
        to = lambda x: int(x.split(":")[0]) * 3600 + int(x.split(":")[1]) * 60 + float(x.split(":")[2])  # noqa: E731
        assert to(a) >= prev_end - 0.011 and to(b) > to(a), line
        prev_end = to(b)
print("captions ok")
print("ALL TESTS PASSED")
