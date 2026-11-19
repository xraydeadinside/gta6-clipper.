"""Local check of the editor without Twitch/Gemini: python scripts/test_render.py [video.mp4]
Builds a fake 'stream' (game + webcam box in the top-right) and renders both layouts."""
import itertools
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from clipper import edit  # noqa: E402
from clipper.config import load_config  # noqa: E402

tmp = Path("work/test")
tmp.mkdir(parents=True, exist_ok=True)
src = Path(sys.argv[1]) if len(sys.argv) > 1 else tmp / "src.mp4"
CAM = [1460, 40, 420, 240]
if len(sys.argv) == 1:
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc2=s=1920x1080:r=30:d=24",
        "-f", "lavfi", "-i", "smptebars=s=420x240:r=30:d=24",
        "-f", "lavfi", "-i", "sine=f=300:r=48000:d=24",
        "-filter_complex",
        "[1:v]drawtext=text='WEBCAM':fontsize=60:fontcolor=white:borderw=4:x=(w-tw)/2:y=(h-th)/2[cam];"
        f"[0:v][cam]overlay={CAM[0]}:{CAM[1]}[v];"
        "[2:a]volume='if(between(t,4,6)+between(t,14,15.5),0,1)':eval=frame[a]",
        "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
        "-shortest", str(src)], check=True)

vocab = "bro what are you doing no way he just jumped off the bridge with the cops lmao this is insane".split()
words, t = [], 0.3
for w in itertools.cycle(vocab):
    if t > 23:
        break
    if 4 <= t < 6 or 14 <= t < 15.5:
        t += 0.4
        continue
    words.append({"w": w, "s": round(t, 2), "e": round(t + 0.28, 2)})
    t += 0.36

cfg = load_config()["edit"]
sil = edit.detect_silences(src, cfg["silence_db"], cfg["silence_min"])
emph = [5, 20, 33]
focus = [{"t": 3, "x": 0.25, "y": 0.6}, {"t": 9, "x": 0.7, "y": 0.4}, {"t": 17, "x": 0.4, "y": 0.7}]
callouts_src = [{"t": 10.5, "text": "NO WAY"}, {"t": 19, "text": "HE'S COOKED"}]

for name, cam in (("stack", CAM), ("blur", None)):
    segs = edit.plan_segments(1.0, 23.5, sil, words, emph, cfg, reactions=[12.0], focus=focus, has_cam=bool(cam))
    wo = edit.map_words(words, segs, emph)
    co = [{"t": edit.map_time(c["t"], segs), "text": c["text"]} for c in callouts_src
          if edit.map_time(c["t"], segs) is not None]
    if name == "stack":
        for s in segs:
            print(f"{s['a']:6.2f}-{s['b']:6.2f} {s['shot']:5} {s['cut']:5} z {s['z0']:.2f}->{s['z1']:.2f} c {s['c0']}->{s['c1']}")
    edit.render(src, tmp / f"short_{name}.mp4", segs, wo, cfg, hook="HE DID NOT EXPECT THIS",
                credit="twitch.tv/example", facecam=cam, callouts=co, work=tmp)
    print("OK", tmp / f"short_{name}.mp4")
