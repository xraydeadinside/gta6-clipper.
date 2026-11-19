"""Local speech-to-text with word timestamps (faster-whisper, CPU, free)."""
from pathlib import Path

_model = {}


def transcribe(path: Path, model_name: str = "small.en", compute_type: str = "int8") -> list[dict]:
    """Return [{"w": word, "s": start, "e": end}] in source-clip seconds."""
    from faster_whisper import WhisperModel
    key = (model_name, compute_type)
    if key not in _model:
        _model[key] = WhisperModel(model_name, device="cpu", compute_type=compute_type)
    segments, _ = _model[key].transcribe(
        str(path), word_timestamps=True, vad_filter=True, beam_size=5,
        condition_on_previous_text=False,
    )
    words = []
    for seg in segments:
        for w in seg.words or []:
            text = w.word.strip()
            if text:
                words.append({"w": text, "s": round(w.start, 3), "e": round(w.end, 3)})
    return words


def as_numbered_text(words: list[dict]) -> str:
    """Compact transcript for the LLM: [index|time] word ..."""
    lines, line = [], []
    for i, w in enumerate(words):
        line.append(f"{i}:{w['w']}")
        if len(line) >= 12:
            lines.append(f"[{words[i - len(line) + 1]['s']:.1f}s] " + " ".join(line))
            line = []
    if line:
        lines.append(f"[{words[len(words) - len(line)]['s']:.1f}s] " + " ".join(line))
    return "\n".join(lines) or "(no speech)"
