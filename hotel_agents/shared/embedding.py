"""Wrapper around sentence-transformers/all-MiniLM-L6-v2 — the 384-D embedding
model ST2 uses. Cached on disk so repeated training runs don't re-download.

The encoder consumes:
  - archetype descriptions (target vectors for FamilyEncoder)
  - activity descriptions (built into activity_bank.pt for FitScorer)

Both are deterministic / one-time. We don't embed family field values
themselves — those are categorical and handled by learned embedding tables
in encoder.py.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np
import torch

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
EMBED_DIM = 384


@lru_cache(maxsize=1)
def _get_model():
    from sentence_transformers import SentenceTransformer
    print(f"[embedding] loading {MODEL_NAME}...")
    return SentenceTransformer(MODEL_NAME)


def encode_texts(texts: list[str]) -> torch.Tensor:
    """Encode a list of strings to a (N, 384) float32 tensor.

    Output is L2-normalized so dot product == cosine similarity.
    """
    model = _get_model()
    arr = model.encode(texts, convert_to_numpy=True, normalize_embeddings=True)
    return torch.from_numpy(np.ascontiguousarray(arr)).float()


def encode_text(text: str) -> torch.Tensor:
    """Single-string convenience: returns a (384,) tensor."""
    return encode_texts([text])[0]


def cached_encode(texts: list[str], cache_path: Path) -> torch.Tensor:
    """Embed texts once and persist as a .pt file. On subsequent calls,
    reload from disk if the cache key (count + first 3 hash) matches.
    """
    key = f"{len(texts)}-" + "-".join(str(hash(t))[:6] for t in texts[:3])
    if cache_path.exists():
        cached = torch.load(cache_path, map_location="cpu", weights_only=False)
        if cached.get("key") == key:
            return cached["vectors"]
    vecs = encode_texts(texts)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    torch.save({"key": key, "vectors": vecs}, cache_path)
    return vecs
