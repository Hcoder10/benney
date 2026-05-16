"""JSON/JSONL on-disk storage for cohort data.

Single source of truth for paths. All scripts read/write via these helpers.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Files
ACTIVITIES_PATH = DATA_DIR / "activities_bay.json"
ARCHETYPES_PATH = DATA_DIR / "archetypes.json"
FAMILIES_PATH = DATA_DIR / "families.jsonl"
ITINERARIES_PATH = DATA_DIR / "itineraries.jsonl"
ANCHORS_PATH = DATA_DIR / "anchors.jsonl"   # family_id → archetype_id
CHECKPOINTS_DIR = DATA_DIR.parent / "checkpoints"
CHECKPOINTS_DIR.mkdir(parents=True, exist_ok=True)


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def append_jsonl(path: Path, records: Iterable[dict]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("a", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            count += 1
    return count


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def jsonl_count(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8") as f:
        return sum(1 for line in f if line.strip())
