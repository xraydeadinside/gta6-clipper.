import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
FONTS_DIR = ROOT / "assets" / "fonts"
WORK = Path(os.environ.get("CLIPPER_WORK", ROOT / "work"))
OUT = Path(os.environ.get("CLIPPER_OUT", ROOT / "out"))


def load_config(path: Path | None = None) -> dict:
    path = path or ROOT / "config.yaml"
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def env(name: str, default: str | None = None, required: bool = False) -> str | None:
    val = os.environ.get(name, default)
    if required and not val:
        raise RuntimeError(f"Missing secret/env var: {name}")
    return val
