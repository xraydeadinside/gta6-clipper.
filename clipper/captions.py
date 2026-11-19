"""Word-by-word 'karaoke' captions (ASS) in the style retention-optimized Shorts use."""
import re

W, H = 1080, 1920
YELLOW = "&H0000D7FF&"   # BGR -> #FFD700
RED = "&H003C3CFF&"      # BGR -> #FF3C3C
WHITE = "&H00FFFFFF&"


def ts(t: float) -> str:
    t = max(0.0, t)
    h = int(t // 3600)
    m = int(t % 3600 // 60)
    s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def clean(word: str) -> str:
    w = re.sub(r"[{}\\]", "", word)
    w = re.sub(r"[^\w'?!$%&\-]", "", w, flags=re.U)
    return w.upper()


def chunk_words(words: list[dict], per: int = 2, max_chars: int = 13, gap: float = 0.35) -> list[list[dict]]:
    chunks, cur = [], []
    for w in words:
        if not clean(w["w"]):
            continue
        if cur:
            prev = cur[-1]
            text_len = sum(len(clean(x["w"])) + 1 for x in cur) + len(clean(w["w"]))
            if (len(cur) >= per or w["s"] - prev["e"] > gap or text_len > max_chars
                    or prev["w"].rstrip().endswith((".", "?", "!", ","))):
                chunks.append(cur)
                cur = []
        cur.append(w)
    if cur:
        chunks.append(cur)
    return chunks


HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,Anton,150,&H00FFFFFF,&H00FFFFFF,&H00000000,&H90000000,0,0,0,0,100,100,2,0,1,9,5,5,40,40,0,1
Style: Hook,Anton,170,&H00FFFFFF,&H00FFFFFF,&H00000000,&HA0000000,0,0,0,0,100,100,2,0,1,12,7,8,40,40,110,1
Style: Shout,Anton,165,&H00FFFFFF,&H00FFFFFF,&H00000000,&HA0000000,0,0,0,0,100,100,2,0,1,11,6,5,40,40,0,1
Style: Credit,Anton,44,&H30FFFFFF,&H30FFFFFF,&H60000000,&H00000000,0,0,0,0,100,100,1,0,1,3,0,2,40,40,64,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def build_ass(words: list[dict], duration: float, hook: str = "", hook_seconds: float = 2.8,
              credit: str = "", per: int = 2, y: int = 1250, callouts: list[dict] | None = None,
              callout_y: int = 900, hook_y: int | None = None) -> str:
    """words: [{"w","s","e","emph"}] already in OUTPUT timeline. callouts: [{"t","text"}] in OUTPUT timeline."""
    lines = [HEADER]
    if hook:
        # SCREAMING headline: huge, thick black outline, pop-in + wiggle, 2nd line yellow
        pos = f"\\an5\\pos(540,{hook_y})" if hook_y else ""
        anim = pos + (r"\fscx55\fscy55\frz-6\t(0,110,\fscx112\fscy112\frz3)\t(110,190,\fscx100\fscy100\frz-2)"
                r"\t(190,260,\frz0)")
        lines.append(f"Dialogue: 3,{ts(0)},{ts(min(hook_seconds, duration))},Hook,,0,0,0,,{{{anim}}}{clean_hook(hook)}")
    for k, c in enumerate(callouts or []):
        t0 = float(c["t"])
        if t0 < hook_seconds or t0 >= duration - 0.3:
            continue
        rot = 5 if k % 2 else -5
        txt = shout_text(c["text"])
        if not txt:
            continue
        anim = (f"\\an5\\pos(540,{callout_y})\\fscx40\\fscy40\\frz{-rot}"
                f"\\t(0,100,\\fscx118\\fscy118\\frz{rot})\\t(100,170,\\fscx100\\fscy100\\frz{rot // 2})")
        lines.append(f"Dialogue: 3,{ts(t0)},{ts(min(t0 + 1.0, duration))},Shout,,0,0,0,,{{{anim}\\c{YELLOW}}}{txt}")
    if credit:
        lines.append(f"Dialogue: 1,{ts(0)},{ts(duration)},Credit,,0,0,0,,{credit}")

    chunks = chunk_words(words, per=per)
    for ci, ch in enumerate(chunks):
        next_start = chunks[ci + 1][0]["s"] if ci + 1 < len(chunks) else duration
        chunk_end = min(ch[-1]["e"] + 0.30, next_start, duration)
        for j, w in enumerate(ch):
            start = ch[0]["s"] if j == 0 else w["s"]
            end = ch[j + 1]["s"] if j + 1 < len(ch) else chunk_end
            if end - start < 0.04:
                continue
            parts = []
            for k, x in enumerate(ch):
                t = clean(x["w"])
                if k == j:
                    color = RED if x.get("emph") else YELLOW
                    scale = r"\fscx118\fscy118" if x.get("emph") else ""
                    parts.append(f"{{\\c{color}{scale}}}{t}{{\\c{WHITE}\\fscx100\\fscy100}}")
                else:
                    parts.append(t)
            anim = r"\fscx72\fscy72\t(0,90,\fscx108\fscy108)\t(90,150,\fscx100\fscy100)" if j == 0 else ""
            lines.append(f"Dialogue: 0,{ts(start)},{ts(end)},Cap,,0,0,0,,{{\\an5\\pos(540,{y}){anim}}}" + " ".join(parts))
    return "\n".join(lines) + "\n"


def shout_text(text: str) -> str:
    """Uppercase, strip emoji/markup (the font has no emoji glyphs)."""
    text = re.sub(r"[{}\\]", "", str(text)).upper()
    text = re.sub(r"[^A-Z0-9 '?!.,$%&#@\-]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def clean_hook(text: str) -> str:
    text = shout_text(text)
    if len(text) > 12 and " " in text:   # two lines, second line yellow
        words = text.split()
        best = min(range(1, len(words)),
                   key=lambda k: abs(len(" ".join(words[:k])) - len(" ".join(words[k:]))))
        l1, l2 = " ".join(words[:best]), " ".join(words[best:])
    else:
        l1, l2 = text, ""
    longest = max(len(l1), len(l2))
    size = "" if longest <= 11 else f"{{\\fs{max(100, int(170 * 11 / longest))}}}"
    return size + l1 + (f"\\N{{\\c{YELLOW}}}{l2}" if l2 else "")
