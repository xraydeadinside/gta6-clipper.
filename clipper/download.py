"""Download Twitch clips / VOD sections with yt-dlp (free)."""
import subprocess
from pathlib import Path

import yt_dlp
from yt_dlp.utils import download_range_func


def download(url: str, out_path: Path, start: float | None = None, end: float | None = None) -> tuple[Path, dict]:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    opts = {
        "outtmpl": str(out_path.with_suffix("")) + ".%(ext)s",
        "format": "best[height<=1080][ext=mp4]/best[height<=1080]/best",
        "quiet": True,
        "no_warnings": True,
        "merge_output_format": "mp4",
        "retries": 5,
    }
    if start is not None and end is not None:
        opts["download_ranges"] = download_range_func(None, [(start, end)])
        opts["force_keyframes_at_cuts"] = True
    with yt_dlp.YoutubeDL(opts) as y:
        info = y.extract_info(url, download=True)
        path = Path(y.prepare_filename(info))
    if not path.exists():
        cands = sorted(out_path.parent.glob(out_path.stem + ".*"))
        path = cands[0]
    if path.suffix != ".mp4":
        mp4 = path.with_suffix(".mp4")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(path), "-c", "copy", str(mp4)], check=True)
        path = mp4
    meta = {k: info.get(k) for k in ("uploader", "uploader_id", "creator", "title", "view_count", "duration")}
    return path, meta


def probe_duration(path: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True, check=True)
    return float(r.stdout.strip())
