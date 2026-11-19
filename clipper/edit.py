"""ADHD-style Shorts editor on pure ffmpeg (runs on a free GitHub runner, no GPU).

Virtual camera over a landscape stream clip:
  * layout: BIG facecam on top + gameplay below (or blurred bg + gameplay if no facecam)
  * gameplay panel is a moving camera: fast-but-smooth eased push-ins and pans to where the action is
  * short REACTION shots: the streamer's webcam zoomed in full-screen right after punchlines / big reactions
  * cuts real silences only (loud gameplay is never cut), jump cuts alternate hard zoom / smooth glide
  * karaoke captions, SCREAMING outlined headline + pop-up callouts, whoosh/pop SFX, progress bar, -14 LUFS
"""
import re
import subprocess
from pathlib import Path

from .captions import build_ass
from .config import FONTS_DIR

OW, OH = 1080, 1920
SW, SH = 1920, 1080  # every source is normalized to this first


def run(cmd: list[str]) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {' '.join(cmd[:6])}...\n{r.stderr[-3000:]}")
    return r.stderr


# ---------------------------------------------------------------- analysis
def detect_silences(src: Path, db: float, min_len: float) -> list[tuple[float, float]]:
    err = run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(src), "-af",
               f"silencedetect=noise={db}dB:d={min_len}", "-f", "null", "-"])
    out, start = [], None
    for line in err.splitlines():
        m = re.search(r"silence_start: ([\d.]+)", line)
        if m:
            start = float(m.group(1))
        m = re.search(r"silence_end: ([\d.]+)", line)
        if m and start is not None:
            out.append((start, float(m.group(1))))
            start = None
    if start is not None:
        out.append((start, 1e9))
    return out


# ---------------------------------------------------------------- planning
def _keep_intervals(ts, te, silences, cfg):
    pad = 0.12
    keep = [(ts, te)]
    for sa, sb in silences:
        ca, cb = sa + pad, sb - pad
        if cb - ca < cfg["silence_min"] - 2 * pad:
            continue
        nxt = []
        for a, b in keep:
            if cb <= a or ca >= b:
                nxt.append((a, b))
                continue
            if ca > a:
                nxt.append((a, ca))
            if cb < b:
                nxt.append((cb, b))
        keep = nxt
    keep = [(a, b) for a, b in keep if b - a >= 0.25]
    return keep or [(ts, te)]


def _focus_at(focus: list[dict], t: float):
    near = [f for f in focus if abs(f["t"] - t) <= 3.0]
    if not near:
        return None
    f = min(near, key=lambda f: abs(f["t"] - t))
    return (f["x"], f["y"])


def plan_segments(ts: float, te: float, silences: list[tuple[float, float]], words: list[dict],
                  emphasis: list[int], cfg: dict, *, reactions: list[float] | None = None,
                  focus: list[dict] | None = None, has_cam: bool = False) -> list[dict]:
    """Return segments in SOURCE seconds with a shot type and a camera move each."""
    reactions, focus = reactions or [], focus or []
    keep = _keep_intervals(ts, te, silences, cfg)
    emph_w = [words[i] for i in emphasis if 0 <= i < len(words)]
    emph_starts = sorted(round(w["s"], 3) for w in emph_w)
    word_ends = sorted(w["e"] for w in words)

    # reaction windows (webcam full-screen): right after punchlines + where Gemini saw big reactions
    react_len = cfg.get("react_len", 1.0)
    react = []
    if has_cam and cfg.get("react_shots", True):
        cands = sorted([w["e"] + 0.05 for w in emph_w] + list(reactions))
        for r in cands:
            if r < ts + cfg.get("hook_seconds", 2.8) + 0.3 or r + 0.5 > te:  # no face close-up under the headline
                continue
            if react and r - react[-1] < cfg.get("react_min_gap", 4.0):
                continue
            react.append(round(r, 3))

    pieces = []
    for a, b in keep:
        cuts = set()
        react_here = []
        for r in react:
            if a + 0.3 < r < b - 0.5:
                end = min(r + react_len, b)
                cuts |= {r, round(end, 3)} if end < b - 0.25 else {r}
                react_here.append((r, end))
        for s in emph_starts:
            if a + 0.3 < s < b - 0.3 and not any(r0 - 0.05 <= s < r1 for r0, r1 in react_here):
                cuts.add(s)
        t = a + cfg["fake_cut_every"]
        while t < b - 1.2:
            near = [e for e in word_ends if abs(e - t) < 0.8 and a + 0.8 < e < b - 0.8]
            c = min(near, key=lambda e: abs(e - t)) if near else t
            if all(abs(c - x) > 0.7 for x in cuts) and not any(r0 <= c < r1 for r0, r1 in react_here):
                cuts.add(round(c, 3))
            t = c + cfg["fake_cut_every"]
        pts = [a] + sorted(cuts) + [b]
        for x, y in zip(pts, pts[1:]):
            if y - x >= 0.2:
                shot = "react" if any(abs(x - r0) < 0.01 for r0, _ in react_here) else "game"
                pieces.append({"a": round(x, 3), "b": round(y, 3), "shot": shot,
                               "emph": shot == "game" and any(abs(x - s) < 0.01 for s in emph_starts)})

    # Shorts length cap
    total, capped = 0.0, []
    for p in pieces:
        room = cfg["max_seconds"] - total
        if room <= 0.3:
            break
        p["b"] = round(min(p["b"], p["a"] + room), 3)
        capped.append(p)
        total += p["b"] - p["a"]

    # camera moves
    pattern, mt, pan = cfg["zoom_pattern"], cfg.get("move_time", 0.35), cfg.get("pan_amount", 0.06)
    gi = 0
    for i, p in enumerate(capped):
        prev = capped[i - 1] if i else None
        contiguous = prev is not None and abs(p["a"] - prev["b"]) < 0.01
        if p["shot"] == "react":
            p.update(z0=1.0, z1=cfg.get("react_zoom", 1.18), c0=None, c1=None, T=0.28,
                     cut="first" if i == 0 else "react")
            continue
        base = pattern[gi % len(pattern)]
        target = _focus_at(focus, (p["a"] + p["b"]) / 2) or (0.5 + (pan if gi % 2 else -pan), 0.5)
        last_game = next((q for q in reversed(capped[:i]) if q["shot"] == "game"), None)
        if i == 0:
            p.update(z0=base, z1=base + 0.06, c0=target, c1=target, T=max(0.3, p["b"] - p["a"]), cut="first")
        elif p["emph"]:  # fast eased punch-in on the punchline, camera flies to the action
            start_c = last_game["c1"] if (contiguous and last_game) else target
            p.update(z0=base, z1=cfg["emphasis_zoom"], c0=start_c, c1=target, T=mt, cut="emph")
        elif contiguous and last_game and prev["shot"] == "game" and prev.get("cut") != "glide":
            # smooth glide: keep zoom continuous, pan/zoom to new framing
            p.update(z0=last_game["z1"], z1=base + 0.08, c0=last_game["c1"], c1=target, T=mt * 1.4, cut="glide")
        else:  # hard jump cut with a zoom change + slow drift (never a static frame)
            p.update(z0=base, z1=base + 0.05, c0=target, c1=target, T=max(0.3, p["b"] - p["a"]), cut="hard")
        gi += 1
    return capped


def plan_from_beats(beats: list[dict], silences: list[tuple[float, float]], words: list[dict],
                    cfg: dict, *, has_cam: bool = False) -> list[dict]:
    """Turn the AI editor's beat plan into segments with shots and camera moves.
    filler beats are cut, silences inside beats are cut, long beats get extra jump cuts."""
    mt = cfg.get("move_time", 0.35)
    word_ends = sorted(w["e"] for w in words)
    pieces = []
    for b in beats:
        if b["role"] == "filler":
            continue
        for a, e in _keep_intervals(b["start"], b["end"], silences, cfg):
            shot = "react" if (b["shot"] == "cam" and has_cam) else "game"
            if shot == "react" and e - a > 2.0:  # face close-up max 2 s, then back to the game
                pieces.append({"a": a, "b": a + 1.6, "beat": b, "shot": "react", "sub": 0})
                a, shot = a + 1.6, "game"
            # extra jump cuts inside long game beats (snapped to word ends)
            cuts, t = [], a + cfg["fake_cut_every"]
            while shot == "game" and t < e - 1.2:
                near = [w for w in word_ends if abs(w - t) < 0.8 and a + 0.8 < w < e - 0.8]
                c = min(near, key=lambda w: abs(w - t)) if near else t
                cuts.append(c)
                t = c + cfg["fake_cut_every"]
            pts = [a] + cuts + [e]
            for k, (x, y) in enumerate(zip(pts, pts[1:])):
                if y - x >= 0.2:
                    pieces.append({"a": round(x, 3), "b": round(y, 3), "beat": b, "shot": shot, "sub": k})

    total, segs = 0.0, []
    for i, p in enumerate(pieces):
        room = cfg["max_seconds"] - total
        if room <= 0.3:
            break
        p["b"] = round(min(p["b"], p["a"] + room), 3)
        total += p["b"] - p["a"]
        b = p.pop("beat")
        prev = segs[-1] if segs else None
        contiguous = prev is not None and abs(p["a"] - prev["b"]) < 0.01
        last_game = next((q for q in reversed(segs) if q["shot"] == "game"), None)
        target, zoom = (b["x"], b["y"]), b["zoom"]
        p["emph"] = b["role"] == "punchline" and p["sub"] == 0
        p["role"] = b["role"]
        if p["shot"] == "react":
            p.update(z0=1.0, z1=cfg.get("react_zoom", 1.18), c0=None, c1=None, T=0.28,
                     cut="first" if not segs else "react")
        elif p["sub"] > 0:  # extra cut inside a beat: alternate zoom so it reads as a cut
            z = max(1.0, zoom - 0.18) if p["sub"] % 2 else zoom
            p.update(z0=z, z1=z + 0.05, c0=target, c1=target, T=max(0.3, p["b"] - p["a"]), cut="hard")
        else:
            cam = b["camera"]
            start_c = last_game["c1"] if (contiguous and last_game) else target
            start_z = last_game["z1"] if (contiguous and last_game) else max(1.0, zoom - 0.25)
            if cam == "push":
                p.update(z0=max(1.0, min(start_z, zoom - 0.2)), z1=zoom, c0=start_c, c1=target, T=mt)
            elif cam == "pan":
                p.update(z0=start_z, z1=zoom, c0=start_c, c1=target, T=mt * 1.4)
            elif cam == "whip":
                p.update(z0=start_z, z1=zoom, c0=start_c, c1=target, T=0.18)
            else:  # hold: slow drift, never a frozen frame
                p.update(z0=zoom, z1=zoom + 0.05, c0=target, c1=target, T=max(0.3, p["b"] - p["a"]))
            p["cut"] = ("first" if not segs else "emph" if p["emph"]
                        else "glide" if (contiguous and cam in ("pan", "whip")) else "hard")
        segs.append(p)
    return segs


def map_words(words: list[dict], segs: list[dict], emphasis: list[int]) -> list[dict]:
    """Move word timestamps from source timeline to output timeline (drop cut words)."""
    out, offset = [], 0.0
    emph = set(emphasis)
    for seg in segs:
        for i, w in enumerate(words):
            mid = (w["s"] + w["e"]) / 2
            if seg["a"] <= mid < seg["b"]:
                out.append({"w": w["w"], "emph": i in emph,
                            "s": round(offset + max(0.0, w["s"] - seg["a"]), 3),
                            "e": round(offset + min(seg["b"], w["e"]) - seg["a"], 3)})
        offset += seg["b"] - seg["a"]
    out.sort(key=lambda x: x["s"])
    return out


def map_time(t: float, segs: list[dict]) -> float | None:
    off = 0.0
    for s in segs:
        if s["a"] <= t < s["b"]:
            return off + t - s["a"]
        off += s["b"] - s["a"]
    return None


# ---------------------------------------------------------------- virtual camera
def _ease(T: float) -> str:
    return f"(1-pow(1-min(t/{T:.3f},1),3))"  # easeOutCubic: fast start, soft landing


def panel(region: tuple[int, int, int, int], pw: int, ph: int, z0: float, z1: float,
          c0, c1, T: float, sharpen: bool = False) -> str:
    """Filter chain: moving/zooming camera inside `region` (px of 1920x1080) -> pw x ph."""
    rx, ry, rw, rh = region
    bw = rh * pw / ph if rw / rh > pw / ph else rw  # width of the z=1 window
    c0 = c0 or ((rx + rw / 2) / SW, (ry + rh / 2) / SH)
    c1 = c1 or c0
    e = _ease(T)
    z = f"({z0:.4f}+({z1 - z0:.4f})*{e})"
    s = f"({pw}*{z}/{bw:.2f})"
    cx = f"(({c0[0] * SW:.1f}+({(c1[0] - c0[0]) * SW:.1f})*{e})-{rx})"
    cy = f"(({c0[1] * SH:.1f}+({(c1[1] - c0[1]) * SH:.1f})*{e})-{ry})"
    chain = (f"crop={rw}:{rh}:{rx}:{ry},"
             f"scale=w='ceil({rw}*{s}/2)*2+2':h='ceil({rh}*{s}/2)*2+2':eval=frame:flags=bicubic,"
             f"crop={pw}:{ph}:x='clip({cx}*iw/{rw}-{pw}/2,0,iw-{pw})':y='clip({cy}*ih/{rh}-{ph}/2,0,ih-{ph})',"
             f"setsar=1")
    if sharpen:
        chain += ",unsharp=5:5:0.8"
    return chain


def _norm() -> str:
    return f"scale={SW}:{SH}:force_original_aspect_ratio=increase,crop={SW}:{SH},setsar=1"


def _clamp_box(box) -> tuple[int, int, int, int]:
    x, y, w, h = [int(v) for v in box]
    x, y = max(0, min(x, SW - 32)), max(0, min(y, SH - 32))
    w, h = max(32, min(w, SW - x)), max(32, min(h, SH - y))
    return x, y, w // 2 * 2, h // 2 * 2


def game_region(cam, pw: int, ph: int) -> tuple[int, int, int, int]:
    """Part of the frame WITHOUT the webcam overlay (so the face isn't shown twice).
    Picks the side (left/right/above/below the cam) that fits the biggest camera window."""
    if not cam:
        return (0, 0, SW, SH)
    x, y, w, h = cam
    opts = [(0, 0, x, SH), (x + w, 0, SW - x - w, SH), (0, 0, SW, y), (0, y + h, SW, SH - y - h)]
    best, best_area = (0, 0, SW, SH), 0
    for rx, ry, rw, rh in opts:
        if rw < 400 or rh < 400:
            continue
        bw = rh * pw / ph if rw / rh > pw / ph else rw
        area = bw * bw * ph / pw
        if area > best_area:
            best, best_area = (rx, ry, rw // 2 * 2, rh // 2 * 2), area
    return best


def shot_filter(seg: dict, cam, cfg: dict, i: int) -> str:
    """Return '<chain>' that turns a normalized 1920x1080 segment into 1080x1920 (uses labels suffixed by i)."""
    top = cfg.get("cam_top_height", 760)
    full = (0, 0, SW, SH)
    if seg["shot"] == "react" and cam:
        return (f"split=2[rb{i}][rf{i}];"
                f"[rb{i}]crop={cam[2]}:{cam[3]}:{cam[0]}:{cam[1]},scale=-2:480,crop=270:480,boxblur=12:1,"
                f"scale={OW}:{OH},eq=brightness=-0.18:saturation=1.3[rbb{i}];"
                f"[rf{i}]{panel(cam, 1080, 1180, seg['z0'], seg['z1'], None, None, seg['T'], sharpen=True)}[rff{i}];"
                f"[rbb{i}][rff{i}]overlay=0:{(OH - 1180) // 2 - 40}")
    if cam:  # big facecam on top, moving camera on the game below
        return (f"split=2[ct{i}][gm{i}];"
                f"[ct{i}]{panel(cam, OW, top, 1.0, 1.04, None, None, max(0.3, seg['b'] - seg['a']), sharpen=True)}[cts{i}];"
                f"[gm{i}]{panel(game_region(cam, OW, OH - top), OW, OH - top, seg['z0'], seg['z1'], seg['c0'], seg['c1'], seg['T'])}[gms{i}];"
                f"[cts{i}][gms{i}]vstack=2")
    gh = 1080
    return (f"split=2[bg{i}][fg{i}];"
            f"[bg{i}]scale=-2:480,crop=270:480,boxblur=10:1,scale={OW}:{OH},eq=brightness=-0.12:saturation=1.25[bgb{i}];"
            f"[fg{i}]{panel(full, OW, gh, seg['z0'], seg['z1'], seg['c0'], seg['c1'], seg['T'])}[fgs{i}];"
            f"[bgb{i}][fgs{i}]overlay=0:{cfg.get('game_top', 400)}")


# ---------------------------------------------------------------- sfx
def make_sfx(dir_: Path) -> tuple[Path, Path]:
    dir_.mkdir(parents=True, exist_ok=True)
    whoosh, pop = dir_ / "whoosh.wav", dir_ / "pop.wav"
    if not whoosh.exists():
        run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "anoisesrc=d=0.32:c=pink:a=0.6:r=48000",
             "-af", "highpass=f=500,lowpass=f=5000,afade=t=in:d=0.12,afade=t=out:st=0.14:d=0.18,volume=0.5",
             "-ac", "2", str(whoosh)])
    if not pop.exists():
        run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=f=1100:d=0.09:r=48000",
             "-af", "afade=t=out:st=0.02:d=0.07,volume=0.6", "-ac", "2", str(pop)])
    return whoosh, pop


# ---------------------------------------------------------------- render
def text_positions(cfg: dict, cam) -> dict:
    """Where captions / headline / callouts go so they never cover the streamer's face."""
    if cam:
        top = cfg.get("cam_top_height", 760)
        return {"y": top + int((OH - top) * 0.62),        # captions in the lower part of the game
                "hook_y": top,                             # headline sits on the seam webcam/game
                "callout_y": top + int((OH - top) * 0.32)}
    gt = cfg.get("game_top", 400)
    return {"y": gt + 1080 + 130, "hook_y": None, "callout_y": gt + 540}


def render(src: Path, out: Path, segs: list[dict], words_out: list[dict], cfg: dict, *, hook: str = "",
           credit: str = "", facecam=None, callouts: list[dict] | None = None, work: Path) -> Path:
    work.mkdir(parents=True, exist_ok=True)
    cam = _clamp_box(facecam) if facecam else None
    dur = round(sum(s["b"] - s["a"] for s in segs), 3)
    ass = work / f"{out.stem}.ass"
    ass.write_text(build_ass(words_out, dur, hook=hook, hook_seconds=cfg["hook_seconds"],
                             credit=credit if cfg.get("credit", True) else "",
                             per=cfg["words_per_caption"], callouts=callouts or [],
                             **text_positions(cfg, cam)), encoding="utf-8")

    n = len(segs)
    fc = [f"[0:v]{_norm()},split={n}" + "".join(f"[vi{i}]" for i in range(n)),
          f"[0:a]asplit={n}" + "".join(f"[ai{i}]" for i in range(n))]
    for i, s in enumerate(segs):
        fc.append(f"[vi{i}]trim=start={s['a']}:end={s['b']},setpts=PTS-STARTPTS,fps={cfg['fps']},"
                  f"{shot_filter(s, cam, cfg, i)},setsar=1,format=yuv420p[v{i}]")
        fc.append(f"[ai{i}]atrim=start={s['a']}:end={s['b']},asetpts=PTS-STARTPTS,"
                  f"aformat=sample_rates=48000:channel_layouts=stereo[a{i}]")
    fc.append("".join(f"[v{i}][a{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=1[vc][ac]")

    ass_path = str(ass).replace("\\", "/").replace(":", r"\:")
    fonts = str(FONTS_DIR).replace("\\", "/").replace(":", r"\:")
    vchain = f"[vc]ass='{ass_path}':fontsdir='{fonts}'"
    if cfg.get("progress_bar", True):
        fc.append(f"{vchain}[vs]")
        fc.append(f"color=c=0xFFD400:s={OW}x14:r={cfg['fps']}:d={dur}[pb]")
        fc.append(f"[vs][pb]overlay=x='-w+w*t/{dur}':y=H-h:eof_action=pass,format=yuv420p[vout]")
    else:
        fc.append(f"{vchain},format=yuv420p[vout]")

    inputs = ["-i", str(src)]
    if cfg.get("sfx", True):
        whoosh, pop = make_sfx(work / "sfx")
        inputs += ["-i", str(whoosh), "-i", str(pop)]
        hits, t = [], 0.0
        for i, s in enumerate(segs):
            if i > 0:
                kind = s.get("cut", "hard")
                if kind == "emph":
                    hits.append(("pop", t, 0.7))
                    hits.append(("whoosh", t, 0.35))
                elif kind == "glide":
                    hits.append(("whoosh", t, 0.3))
                else:
                    hits.append(("whoosh", t, 0.55))
            t += s["b"] - s["a"]
        mix = ["[ac]"]
        for name, idx in (("whoosh", 1), ("pop", 2)):
            hs = [h for h in hits if h[0] == name]
            if not hs:
                continue
            fc.append(f"[{idx}:a]asplit={len(hs)}" + "".join(f"[{name}{k}]" for k in range(len(hs))))
            for k, (_, ht, vol) in enumerate(hs):
                ms = max(0, int(ht * 1000) - (90 if name == "whoosh" else 0))
                fc.append(f"[{name}{k}]adelay={ms}|{ms},volume={vol}[{name}d{k}]")
                mix.append(f"[{name}d{k}]")
        fc.append("".join(mix) + f"amix=inputs={len(mix)}:normalize=0:duration=first,"
                                 "loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[aout]")
    else:
        fc.append("[ac]loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[aout]")

    graph = work / f"{out.stem}.filter"
    graph.write_text(";\n".join(fc), encoding="utf-8")
    out.parent.mkdir(parents=True, exist_ok=True)
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *inputs,
         "-filter_complex_script", str(graph), "-map", "[vout]", "-map", "[aout]",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-maxrate", "6M", "-bufsize", "12M",
         "-profile:v", "high", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
         "-t", str(dur), "-movflags", "+faststart", str(out)])
    return out


def prepend_intro(intro: Path, main: Path, out: Path, max_intro: float = 3.5) -> Path:
    """Put a Flow-generated intro (any aspect) in front of the edited short."""
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(intro)],
                       capture_output=True, text=True)
    d = min(max_intro, float(r.stdout.strip() or max_intro))
    has_audio = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
                                "-of", "csv=p=0", str(intro)], capture_output=True, text=True).stdout.strip() != ""
    aud = (f"[0:a]atrim=0:{d},asetpts=PTS-STARTPTS,aformat=sample_rates=48000:channel_layouts=stereo,"
           f"apad=whole_dur={d}[sil];" if has_audio else
           f"anullsrc=r=48000:cl=stereo,atrim=0:{d}[sil];")
    fc = (f"[0:v]trim=0:{d},setpts=PTS-STARTPTS,scale={OW}:{OH}:force_original_aspect_ratio=increase,"
          f"crop={OW}:{OH},fps=30,setsar=1,format=yuv420p[iv];" + aud +
          "[1:v]setsar=1,format=yuv420p[mv];[1:a]aformat=sample_rates=48000:channel_layouts=stereo[ma];"
          "[iv][sil][mv][ma]concat=n=2:v=1:a=1[v][a]")
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(intro), "-i", str(main), "-filter_complex", fc,
         "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
         "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out)])
    return out


def preview(src: Path, out: Path) -> Path:
    """Small copy for Telegram (bot upload limit 50 MB, fast to watch on phone)."""
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-vf", "scale=540:960", "-c:v", "libx264",
         "-preset", "veryfast", "-crf", "27", "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(out)])
    return out
