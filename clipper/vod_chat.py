"""Find hot moments in a finished VOD by chat spikes (free, no API key).

Idea: chat reacts 2-10 s AFTER the moment. We count messages in 10-second buckets,
weight laughs/hype emotes higher, find peaks above baseline and backdate the window.
"""
import re
import statistics

LAUGH = re.compile(r"\b(lol+|lmao+|lmf?ao|kekw|omegalul|lul|icant|💀|😂|🤣|haha+|xd+|dead|crying)\b", re.I)
HYPE = re.compile(r"\b(pog+|poggers|clip\s*it|clip that|omg|wtf|no way|holy|insane|w+\b|bro what)\b", re.I)


def fetch_chat(vod_url: str) -> list[tuple[float, str]]:
    from chat_downloader import ChatDownloader
    msgs = []
    for m in ChatDownloader().get_chat(vod_url):
        t = m.get("time_in_seconds")
        if t is not None:
            msgs.append((float(t), m.get("message") or ""))
    return msgs


def find_spikes(msgs: list[tuple[float, str]], bucket: float = 10.0, top: int = 8,
                min_gap: float = 90.0, factor: float = 2.2) -> list[dict]:
    """Return [{start, end, peak, score, kind}] sorted by score desc."""
    if not msgs:
        return []
    n = int(max(t for t, _ in msgs) // bucket) + 1
    score = [0.0] * n
    laughs = [0] * n
    for t, text in msgs:
        i = int(t // bucket)
        w = 1.0
        if LAUGH.search(text):
            w += 1.5
            laughs[i] += 1
        if HYPE.search(text):
            w += 1.0
        score[i] += w
    # smooth over 3 buckets
    sm = [sum(score[max(0, i - 1):i + 2]) / len(score[max(0, i - 1):i + 2]) for i in range(n)]
    base = statistics.median([s for s in sm if s > 0] or [1.0])
    cands = sorted(range(n), key=lambda i: sm[i], reverse=True)
    picked: list[dict] = []
    for i in cands:
        if sm[i] < base * factor or len(picked) >= top:
            break
        peak = i * bucket + bucket / 2
        if any(abs(peak - p["peak"]) < min_gap for p in picked):
            continue
        lw = sum(laughs[max(0, i - 1):i + 2])
        picked.append({
            "peak": peak,
            "start": max(0.0, peak - 35.0),   # moment happened before chat reacted
            "end": peak + 12.0,
            "score": round(sm[i] / base, 2),
            "kind": "funny" if lw >= 3 else "hype",
        })
    return picked
