"""FitScorer — ResNet MLP over (family_vec, activity_vec, slot_embed, history_vec) → fit logit.

Mirrors ST2's ResNetAnswerPredictor architecture (same arch class, different
input/output shape). The "super-quick transformer" is in fact a small
residual MLP — ST2 measured it as the best speed/quality tradeoff vs
attention or linear variants for ~250K params.

Inputs (all 384-D):
  family_vec    — output of FamilyEncoder for the guest
  activity_vec  — embedding of the candidate activity's description
  slot_embed    — learned 30-slot lookup (0..29 for a 5-day trip)
  history_vec   — mean-pool of activity_vecs already locked into the chain
                  (zeros for slot 0)

Output:
  fit_logit ∈ ℝ (apply sigmoid for [0,1] probability)
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import torch
import torch.nn as nn
import torch.nn.functional as F


@dataclass
class FitScorerConfig:
    embed_dim: int = 384        # family / activity / slot / history all 384-D
    hidden_dim: int = 512
    n_blocks: int = 2
    dropout: float = 0.3
    n_slots: int = 30


class _ResBlock(nn.Module):
    def __init__(self, hidden: int, dropout: float):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(hidden, hidden),
            nn.ReLU(),
            nn.LayerNorm(hidden),
            nn.Dropout(dropout),
            nn.Linear(hidden, hidden),
            nn.LayerNorm(hidden),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return F.relu(x + self.net(x))


class FitScorer(nn.Module):
    def __init__(self, cfg: FitScorerConfig = FitScorerConfig()):
        super().__init__()
        self.cfg = cfg
        d = cfg.embed_dim
        # Slot embedding table: 30 slots → 384-D
        self.slot_embed = nn.Embedding(cfg.n_slots, d)
        # 4 × d concatenated → hidden_dim → residual stack → scalar
        self.input_proj = nn.Linear(4 * d, cfg.hidden_dim)
        self.input_norm = nn.LayerNorm(cfg.hidden_dim)
        self.blocks = nn.ModuleList([
            _ResBlock(cfg.hidden_dim, cfg.dropout) for _ in range(cfg.n_blocks)
        ])
        self.head = nn.Linear(cfg.hidden_dim, 1)

    def forward(
        self,
        family_vec: torch.Tensor,    # (B, 384)
        activity_vec: torch.Tensor,  # (B, 384)
        slot_idx: torch.Tensor,      # (B,) int64
        history_vec: torch.Tensor,   # (B, 384)
    ) -> torch.Tensor:
        slot = self.slot_embed(slot_idx)         # (B, 384)
        x = torch.cat([family_vec, activity_vec, slot, history_vec], dim=-1)
        x = self.input_norm(F.relu(self.input_proj(x)))
        for block in self.blocks:
            x = block(x)
        return self.head(x).squeeze(-1)          # (B,) logit


# ─────────────────────────────────────────────────────────────────────────────
# IO
# ─────────────────────────────────────────────────────────────────────────────

def save_fit_scorer(model: FitScorer, path: Path) -> None:
    torch.save({
        "state_dict": model.state_dict(),
        "config": {
            "embed_dim": model.cfg.embed_dim,
            "hidden_dim": model.cfg.hidden_dim,
            "n_blocks": model.cfg.n_blocks,
            "dropout": model.cfg.dropout,
            "n_slots": model.cfg.n_slots,
        },
    }, path)


def load_fit_scorer(path: Path, device: str | torch.device = "cpu") -> FitScorer:
    ckpt = torch.load(path, map_location=device, weights_only=False)
    model = FitScorer(FitScorerConfig(**ckpt["config"]))
    model.load_state_dict(ckpt["state_dict"])
    model.to(device)
    model.eval()
    return model
