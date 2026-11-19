"""Finished shorts are stored as GitHub Actions artifacts between runs (free, 14 days)."""
import io
import os
import zipfile
from pathlib import Path

import requests


def _h() -> dict:
    return {"Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}", "Accept": "application/vnd.github+json"}


def fetch(artifact_name: str, filename: str, dest_dir: Path) -> Path:
    repo = os.environ["GITHUB_REPOSITORY"]
    r = requests.get(f"https://api.github.com/repos/{repo}/actions/artifacts",
                     params={"name": artifact_name, "per_page": 5}, headers=_h(), timeout=30)
    r.raise_for_status()
    arts = [a for a in r.json().get("artifacts", []) if not a.get("expired")]
    if not arts:
        raise FileNotFoundError(f"artifact {artifact_name} not found/expired")
    z = requests.get(arts[0]["archive_download_url"], headers=_h(), timeout=600)
    z.raise_for_status()
    dest_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(z.content)) as zf:
        zf.extract(filename, dest_dir)
    return dest_dir / filename


def current_name() -> str:
    return f"renders-{os.environ.get('GITHUB_RUN_ID', 'local')}"
