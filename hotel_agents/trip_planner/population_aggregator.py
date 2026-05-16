"""PopulationAggregator — the online query-time logic.

For each slot in the guest's evolving itinerary:
  1. KNN over the cohort family_vec matrix → top-K similar families
  2. Trajectory filter via Jaccard: keep families whose prefix matches
     the guest's picks so far (≥ 0.5 overlap, with fallback drops)
  3. Tally next-slot picks among the filtered set
  4. Bootstrap confidence intervals + vs-baseline (M1 mitigation)
  5. Apply floor/ceiling treatment to percentages (M3 mitigation)
  6. Return ranked options + metadata for Haiku reasoning lookup

This module is pure-logic; the HTTP layer in server.py wraps it.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import torch
import torch.nn.functional as F

from ..shared.encoder import FamilyEncoder, family_to_indices
from ..shared.schema import Family, TOTAL_SLOTS


# ─────────────────────────────────────────────────────────────────────────────
# Tunables
# ─────────────────────────────────────────────────────────────────────────────

KNN_TOP_K = 1000              # nearest families pre-filter
JACCARD_MIN = 0.5             # minimum prefix overlap to count as "similar trajectory"
JACCARD_FALLBACK_FLOOR = 0.2  # never go below this
MIN_SUBPOPULATION = 50        # if filter leaves fewer, relax threshold
N_TOP_BARS = 5                # surface this many per slot
BOOTSTRAP_ITERS = 100         # CI samples
FLOOR_PCT = 15                # below this → drop into "off the beaten path" bucket
CEILING_PCT = 70              # above this → drop number, label "most popular"
BASELINE_DELTA = 3            # if |similar - baseline| < this, signal is noise


# ─────────────────────────────────────────────────────────────────────────────
# Cohort container — loaded once at server start
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Cohort:
    family_vecs: torch.Tensor       # (F, 384) L2-normalized
    itineraries: np.ndarray         # (F, 30) int32 of activity_bank rows
    family_ids: list[str]
    activity_ids: list[str]
    baseline_per_slot: list[Counter]  # length 30, full-cohort tallies per slot

    @classmethod
    def from_npz(cls, path: Path) -> "Cohort":
        data = np.load(path, allow_pickle=True)
        family_vecs = torch.from_numpy(np.ascontiguousarray(data["family_vecs"])).float()
        # L2 normalize once; KNN becomes a single dot product.
        family_vecs = F.normalize(family_vecs, dim=-1)
        itineraries = data["itineraries"].astype(np.int32)
        family_ids = list(data["family_ids"])
        activity_ids = list(data["activity_ids"])
        baseline = [
            Counter(itineraries[:, slot].tolist())
            for slot in range(itineraries.shape[1])
        ]
        return cls(
            family_vecs=family_vecs,
            itineraries=itineraries,
            family_ids=family_ids,
            activity_ids=activity_ids,
            baseline_per_slot=baseline,
        )

    def baseline_pct(self, slot_idx: int, activity_row: int) -> float:
        ctr = self.baseline_per_slot[slot_idx]
        total = sum(ctr.values()) or 1
        return ctr[activity_row] / total * 100


# ─────────────────────────────────────────────────────────────────────────────
# Aggregator
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ProbabilityOption:
    activity_id: str
    activity_row: int
    pct: float            # 0..100
    ci_low: float
    ci_high: float
    baseline_pct: float
    n: int                # count in filtered subpopulation
    of: int               # subpopulation size
    band: str             # "popular" | "standard" | "niche" | "buried"


@dataclass
class SlotResult:
    slot_idx: int
    subpopulation_size: int
    jaccard_threshold_used: float
    options: list[ProbabilityOption]


class PopulationAggregator:
    def __init__(
        self,
        cohort: Cohort,
        encoder: FamilyEncoder,
        device: str | torch.device = "cpu",
    ):
        self.cohort = cohort
        self.encoder = encoder
        self.device = device
        self._family_vecs_on_device = cohort.family_vecs.to(device)

    # ── Step 1: KNN over the cohort ────────────────────────────────────────
    def knn(self, family_vec: torch.Tensor, k: int = KNN_TOP_K) -> np.ndarray:
        """Return the cohort rows of the top-k most-similar families by cosine."""
        q = F.normalize(family_vec.to(self.device), dim=-1)
        sims = (self._family_vecs_on_device @ q.unsqueeze(-1)).squeeze(-1)
        k = min(k, sims.numel())
        top = torch.topk(sims, k=k).indices.cpu().numpy().astype(np.int64)
        return top

    # ── Step 2: trajectory filter via Jaccard ──────────────────────────────
    def _jaccard_filter(
        self,
        candidate_rows: np.ndarray,
        guest_history: list[int],
        threshold: float = JACCARD_MIN,
    ) -> tuple[np.ndarray, float]:
        if not guest_history:
            return candidate_rows, threshold
        guest_set = set(guest_history)
        guest_len = len(guest_set) or 1
        prefix_len = len(guest_history)

        # For each candidate, compute Jaccard over the same prefix length.
        cohort_prefixes = self.cohort.itineraries[candidate_rows, :prefix_len]
        keep_mask = np.zeros(len(candidate_rows), dtype=bool)
        for i, row in enumerate(cohort_prefixes):
            cand_set = set(int(x) for x in row)
            inter = len(guest_set & cand_set)
            union = len(guest_set | cand_set) or 1
            if inter / union >= threshold:
                keep_mask[i] = True
        filtered = candidate_rows[keep_mask]
        used_thresh = threshold

        # Relax if too few survived
        while len(filtered) < MIN_SUBPOPULATION and used_thresh > JACCARD_FALLBACK_FLOOR:
            used_thresh = round(used_thresh - 0.1, 2)
            keep_mask = np.zeros(len(candidate_rows), dtype=bool)
            for i, row in enumerate(cohort_prefixes):
                cand_set = set(int(x) for x in row)
                inter = len(guest_set & cand_set)
                union = len(guest_set | cand_set) or 1
                if inter / union >= used_thresh:
                    keep_mask[i] = True
            filtered = candidate_rows[keep_mask]

        return filtered, used_thresh

    # ── Step 3-5: tally + CIs + band ───────────────────────────────────────
    def _tally(
        self,
        cohort_rows: np.ndarray,
        slot_idx: int,
    ) -> list[ProbabilityOption]:
        picks = self.cohort.itineraries[cohort_rows, slot_idx]
        total = len(picks)
        if total == 0:
            return []
        counter = Counter(picks.tolist())

        # Bootstrap CIs once for all activities — sample picks WITH replacement
        rng = np.random.default_rng(slot_idx)
        boot = np.empty((BOOTSTRAP_ITERS, len(counter)), dtype=np.float32)
        keys = list(counter.keys())
        key_to_col = {k: i for i, k in enumerate(keys)}
        for b in range(BOOTSTRAP_ITERS):
            sample = rng.choice(picks, size=total, replace=True)
            ctr = Counter(sample.tolist())
            for k in keys:
                boot[b, key_to_col[k]] = ctr.get(k, 0) / total * 100
        ci_low = np.percentile(boot, 5, axis=0)
        ci_high = np.percentile(boot, 95, axis=0)

        # Build options sorted by raw pct
        options: list[ProbabilityOption] = []
        for k, n in counter.most_common():
            col = key_to_col[k]
            pct = n / total * 100
            baseline = self.cohort.baseline_pct(slot_idx, int(k))
            if pct >= CEILING_PCT:
                band = "popular"
            elif pct >= FLOOR_PCT:
                band = "standard"
            else:
                band = "niche"
            # Suppress if personalization isn't moving the needle vs baseline
            if abs(pct - baseline) < BASELINE_DELTA and band != "popular":
                band = "buried"
            options.append(ProbabilityOption(
                activity_id=self.cohort.activity_ids[int(k)],
                activity_row=int(k),
                pct=round(pct, 1),
                ci_low=round(float(ci_low[col]), 1),
                ci_high=round(float(ci_high[col]), 1),
                baseline_pct=round(baseline, 1),
                n=int(n),
                of=total,
                band=band,
            ))
        # Keep top-N for the bars; "off the beaten path" tail is collapsed at the UI level.
        return options[:N_TOP_BARS]

    # ── Public API: one query ──────────────────────────────────────────────
    def next_slot_probabilities(
        self,
        family: Family,
        guest_history: list[str],         # activity_ids the guest has locked so far
    ) -> SlotResult:
        """Compute the ranked probability distribution for the next un-locked slot."""
        # Encode the guest's family
        idx = torch.tensor([family_to_indices(family)], dtype=torch.long)
        with torch.no_grad():
            family_vec = self.encoder(idx.to(self.device))[0]

        # Translate guest's activity_ids → bank rows
        id_to_row = {aid: i for i, aid in enumerate(self.cohort.activity_ids)}
        history_rows = [id_to_row[aid] for aid in guest_history if aid in id_to_row]
        slot_idx = len(history_rows)
        if slot_idx >= TOTAL_SLOTS:
            return SlotResult(slot_idx=slot_idx, subpopulation_size=0,
                              jaccard_threshold_used=0.0, options=[])

        # KNN + filter
        knn_rows = self.knn(family_vec, k=KNN_TOP_K)
        filtered, used_thresh = self._jaccard_filter(knn_rows, history_rows)
        # Tally
        options = self._tally(filtered, slot_idx)
        return SlotResult(
            slot_idx=slot_idx,
            subpopulation_size=int(len(filtered)),
            jaccard_threshold_used=float(used_thresh),
            options=options,
        )


# ─────────────────────────────────────────────────────────────────────────────
# CLI smoke test
# ─────────────────────────────────────────────────────────────────────────────

def _smoke_test() -> None:
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from hotel_agents.shared.encoder import load_encoder
    from hotel_agents.shared.storage import CHECKPOINTS_DIR, DATA_DIR

    cohort = Cohort.from_npz(DATA_DIR / "itineraries_cohort.npz")
    encoder = load_encoder(CHECKPOINTS_DIR / "family_encoder.pt")
    agg = PopulationAggregator(cohort, encoder)

    sample_family: Family = {  # type: ignore[typeddict-item]
        "group_type": "couple", "adult_count": 2, "kid_ages": "none",
        "trip_purpose": "leisure", "budget_tier": "premium",
        "trip_length_days": 5, "pace": "balanced",
        "primary_interest": "tech", "secondary_interest": "food",
        "crowd_tolerance": "okay", "energy": "medium",
        "local_interaction": "mixed", "mobility": "full",
        "dietary": "none", "language_comfort": "english-only",
    }

    history: list[str] = []
    for step in range(3):
        result = agg.next_slot_probabilities(sample_family, history)
        print(f"\n── slot {result.slot_idx} ── "
              f"(subpop={result.subpopulation_size}, "
              f"jaccard_thresh={result.jaccard_threshold_used:.2f})")
        for opt in result.options:
            arrow = "↑" if opt.pct > opt.baseline_pct + BASELINE_DELTA else "·"
            print(f"  {opt.pct:>5.1f}% [{opt.ci_low:>5.1f}-{opt.ci_high:>5.1f}]  "
                  f"vs base {opt.baseline_pct:>5.1f}% {arrow}  "
                  f"({opt.band:>8}) {opt.activity_id}")
        # Lock in the top pick for the next iteration
        if result.options:
            history.append(result.options[0].activity_id)


if __name__ == "__main__":
    _smoke_test()
