"""FamilyEncoder — ST2-style compression model adapted for the 15 categorical
keyword fields of a Family.

ST2's CompressionModel was:
    (10 Q/A pairs of 384-D embeddings) → 2 linear layers → 384-D persona vector

Our analog for categorical inputs:
    15 fields × (learned embedding per field) → concat → linear → 384-D

The "two-layer" recipe is preserved:
    layer 1: per-field embedding table (15 different LUTs)
    layer 2: concat (15 × D_token) → Linear → 384

Trained with cosine-embedding loss against the family's anchor archetype's
text-description embedding (see embedding.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import torch
import torch.nn as nn
import torch.nn.functional as F

from .schema import FAMILY_FIELDS, Family

# Value vocabularies — order matters (must match across training and inference)
FIELD_VOCABS: dict[str, tuple[str, ...]] = {
    "group_type": ("solo", "couple", "family", "friends", "business", "event"),
    "adult_count": ("1", "2", "3", "4+"),
    "kid_ages": ("none", "0-5", "6-12", "13-17", "mixed"),
    "trip_purpose": ("leisure", "business", "mixed", "event", "honeymoon"),
    "budget_tier": ("shoestring", "mid", "premium", "luxury"),
    "trip_length_days": ("1", "2", "3", "4", "5", "6", "7+"),
    "pace": ("relaxed", "balanced", "packed"),
    "primary_interest": ("food", "culture", "nature", "adventure", "tech", "wine", "shopping"),
    "secondary_interest": ("food", "culture", "nature", "adventure", "tech", "wine", "shopping"),
    "crowd_tolerance": ("avoid", "okay", "love"),
    "energy": ("low", "medium", "high"),
    "local_interaction": ("touristy", "mixed", "off-the-beaten-path"),
    "mobility": ("full", "limited", "wheelchair"),
    "dietary": ("none", "veg", "vegan", "kosher", "halal", "gf", "nut-free", "other"),
    "language_comfort": ("english-only", "english-plus", "non-english"),
}

assert set(FIELD_VOCABS.keys()) == set(FAMILY_FIELDS), "vocab must cover all family fields"


# Synonyms Haiku tends to emit that we map onto canonical vocab values.
VALUE_ALIASES: dict[str, dict[str, str]] = {
    "primary_interest": {
        "wellness": "nature",
        "spa": "nature",
        "art": "culture",
        "music": "culture",
        "history": "culture",
        "outdoors": "nature",
        "hiking": "nature",
        "technology": "tech",
        "startup": "tech",
        "drinks": "wine",
        "drink": "wine",
        "spirits": "wine",
    },
    "secondary_interest": {
        "wellness": "nature",
        "spa": "nature",
        "art": "culture",
        "music": "culture",
        "history": "culture",
        "outdoors": "nature",
        "hiking": "nature",
        "technology": "tech",
        "startup": "tech",
        "drinks": "wine",
        "drink": "wine",
        "spirits": "wine",
    },
    "dietary": {
        "vegetarian": "veg",
        "gluten-free": "gf",
        "gluten_free": "gf",
        "glutenfree": "gf",
        "nut_free": "nut-free",
        "nutfree": "nut-free",
        "dairy-free": "other",
        "lactose-intolerant": "other",
        "pescatarian": "other",
    },
    "local_interaction": {
        "off_the_beaten_path": "off-the-beaten-path",
        "off-beaten-path": "off-the-beaten-path",
        "offbeat": "off-the-beaten-path",
    },
}


def _value_to_index(field: str, value) -> int:
    """Find the vocab index for a field's value. Handles ints (counts) by
    coercing to string, with '4+' / '7+' as the bucket for higher values.
    Tolerates common Haiku drift via VALUE_ALIASES.
    """
    vocab = FIELD_VOCABS[field]
    if field == "adult_count":
        n = int(value)
        return vocab.index("4+" if n >= 4 else str(n))
    if field == "trip_length_days":
        n = int(value)
        return vocab.index("7+" if n >= 7 else str(n))
    s = str(value)
    aliases = VALUE_ALIASES.get(field, {})
    if s in aliases:
        s = aliases[s]
    if s not in vocab:
        # Tolerate case/style drift by best-effort match
        ln = s.lower()
        if ln in aliases:
            s = aliases[ln]
        if s not in vocab:
            for i, v in enumerate(vocab):
                if v.lower() == ln:
                    return i
        if s not in vocab:
            raise ValueError(f"unknown value {value!r} for field {field!r}; vocab={vocab}")
    return vocab.index(s)


def family_to_indices(family: Family) -> list[int]:
    return [_value_to_index(f, family[f]) for f in FAMILY_FIELDS]  # type: ignore[literal-required]


@dataclass
class EncoderConfig:
    token_dim: int = 32        # per-field embedding dim
    latent_dim: int = 384      # output family vector (matches MiniLM)
    n_fields: int = 15


class FamilyEncoder(nn.Module):
    """15 token embeddings → concat → linear → 384-D family vector."""

    def __init__(self, cfg: EncoderConfig = EncoderConfig()):
        super().__init__()
        self.cfg = cfg
        # One embedding table per field; vocab sizes differ per field.
        self.field_embeddings = nn.ModuleList([
            nn.Embedding(len(FIELD_VOCABS[f]), cfg.token_dim)
            for f in FAMILY_FIELDS
        ])
        self.proj = nn.Linear(cfg.n_fields * cfg.token_dim, cfg.latent_dim)

    def forward(self, idx: torch.Tensor) -> torch.Tensor:
        """idx: (B, 15) int64. Returns: (B, 384) float32."""
        assert idx.shape[1] == self.cfg.n_fields
        # Look up each field independently, then concat along the token dim.
        toks = [emb(idx[:, i]) for i, emb in enumerate(self.field_embeddings)]
        x = torch.cat(toks, dim=-1)              # (B, 15*token_dim)
        return self.proj(x)                       # (B, 384)


def cosine_loss(pred: torch.Tensor, target: torch.Tensor) -> torch.Tensor:
    """1 - mean cosine similarity. Both tensors should be L2-normalized
    upstream for honesty, but we re-normalize here defensively.
    """
    pred = F.normalize(pred, dim=-1)
    target = F.normalize(target, dim=-1)
    return (1.0 - (pred * target).sum(dim=-1)).mean()


# ─────────────────────────────────────────────────────────────────────────────
# IO helpers
# ─────────────────────────────────────────────────────────────────────────────

def save_encoder(model: FamilyEncoder, path: Path) -> None:
    torch.save({
        "state_dict": model.state_dict(),
        "config": {
            "token_dim": model.cfg.token_dim,
            "latent_dim": model.cfg.latent_dim,
            "n_fields": model.cfg.n_fields,
        },
    }, path)


def load_encoder(path: Path, device: str | torch.device = "cpu") -> FamilyEncoder:
    ckpt = torch.load(path, map_location=device, weights_only=False)
    model = FamilyEncoder(EncoderConfig(**ckpt["config"]))
    model.load_state_dict(ckpt["state_dict"])
    model.to(device)
    model.eval()
    return model
