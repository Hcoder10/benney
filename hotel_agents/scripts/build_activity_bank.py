"""Embed every activity's description into a 384-D vector via the same
sentence-transformer the FamilyEncoder uses. Cached as activity_bank.pt.

Schema of activity_bank.pt:
  {
    "ids":     ["stanford_campus", "filoli_gardens", ...]   # length N
    "vectors": Tensor[N, 384] (L2-normalized)
    "id_to_row": {"stanford_campus": 0, ...}
  }

Consumed by FitScorer training + offline itinerary precompute + the
online scheduler.
"""

from __future__ import annotations

import sys
from pathlib import Path

import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from hotel_agents.shared.embedding import encode_texts  # noqa: E402
from hotel_agents.shared.storage import (  # noqa: E402
    ACTIVITIES_PATH,
    CHECKPOINTS_DIR,
    read_json,
)

BANK_PATH = CHECKPOINTS_DIR / "activity_bank.pt"


def main() -> None:
    activities = read_json(ACTIVITIES_PATH)
    print(f"embedding {len(activities)} activity descriptions...")
    # The description carries the semantic content; pad with name and tags
    # to ensure the vector reflects both "what is it" and "what kind".
    texts = [
        f"{a['name']} — {a['description']} (tags: {', '.join(a['tags'])})"
        for a in activities
    ]
    vectors = encode_texts(texts)                # (N, 384), L2-normalized
    ids = [a["id"] for a in activities]
    bank = {
        "ids": ids,
        "vectors": vectors,
        "id_to_row": {aid: i for i, aid in enumerate(ids)},
    }
    BANK_PATH.parent.mkdir(parents=True, exist_ok=True)
    torch.save(bank, BANK_PATH)
    print(f"saved {len(ids)} activity vectors → {BANK_PATH}")
    print(f"  vector shape: {tuple(vectors.shape)}")
    print(f"  first 5 ids: {ids[:5]}")


if __name__ == "__main__":
    main()
