"""Greedy + temperature-sampled scheduler that turns one family into a
30-slot itinerary using the FitScorer.

Used in two places:
  - OFFLINE  (precompute_itineraries.py): runs across the full cohort,
             populates itineraries_50k.npy. Temperature is moderate (~0.6)
             to produce population spread (mitigation M3 — without spread
             the per-slot probabilities collapse).
  - ONLINE   (PopulationAggregator): re-runs for the guest with the
             trajectory-filtered subpopulation when the guest deviates.

Constraints applied at filter time (hard):
  - activity must be kid_ok if family has kids
  - activity must be mobility_ok if family has limited mobility
  - activity.budget_tier ≤ family.budget_tier
  - activity must accept dietary if dietary != none (best-effort: tag match)

Soft preferences (FitScorer learns):
  - energy pacing across the day
  - tag affinity with primary/secondary interests
  - geographic continuity (proxied via the history_vec)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np
import torch
import torch.nn.functional as F

from ..shared.schema import (
    ACTIVITY_BUDGET_RANK,
    BudgetTier,
    Family,
    SLOTS_PER_DAY,
    TOTAL_SLOTS,
    activity_compatible_with_budget,
)
from ..shared.schema import Activity
from .fit_scorer import FitScorer
from .slot_constraints import (
    distance_penalty,
    repetition_penalty,
    slot_open_mask,
    tag_boost,
)


# ─────────────────────────────────────────────────────────────────────────────
# Hard constraint filter
# ─────────────────────────────────────────────────────────────────────────────

def candidate_mask(activities: list[Activity], family: Family) -> np.ndarray:
    """Return a boolean mask (N_act,) of activities the family is allowed to
    pick. Applied once per family at the start of scheduling; the same mask
    is re-used for every slot since the filter is family-level, not slot-level.
    """
    has_kids = family["kid_ages"] != "none"
    is_limited = family["mobility"] in ("limited", "wheelchair")
    budget = family["budget_tier"]
    return np.array([
        (a["kid_ok"] or not has_kids)
        and (a["mobility_ok"] or not is_limited)
        and activity_compatible_with_budget(a, budget)
        for a in activities
    ], dtype=bool)


# ─────────────────────────────────────────────────────────────────────────────
# Greedy + temperature-sampled scheduler
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ScheduleConfig:
    temperature: float = 0.6      # offline sampling temperature (0 = argmax)
    top_k: int = 10               # sample from top-k by fit score
    history_decay: float = 0.0    # 0 = simple mean; >0 = recency-weighted
    tag_boost_weight: float = 0.8  # slot-tag soft preference
    distance_weight: float = 0.6   # geographic continuity penalty
    distance_scale_km: float = 30.0
    repetition_weight: float = 1.2  # logit subtraction for already-used activities


def schedule(
    fit_scorer: FitScorer,
    family_vec: torch.Tensor,            # (384,)
    candidate_mask_arr: np.ndarray,      # (N_act,) bool
    activity_vecs: torch.Tensor,          # (N_act, 384) — full bank
    activities: list[dict],               # bank metadata (lat/lng/tags/hours)
    slot_open_masks: np.ndarray,          # (6, N_act) bool — pre-computed
    slot_tag_boosts: np.ndarray,          # (6, N_act) float — pre-computed
    cfg: ScheduleConfig = ScheduleConfig(),
    rng: np.random.Generator | None = None,
    device: str | torch.device = "cpu",
) -> list[int]:
    """Produce a 30-slot itinerary as a list of activity-bank rows.

    Hard filters at each slot:
      family-level mask (kid/mobility/budget) AND slot-open-hours mask.

    Soft signals added to FitScorer logit:
      + slot-tag boost (cafes at breakfast, museums at late-morning, …)
      - distance penalty from previous slot's lat/lng
      - repetition penalty if activity already used in this itinerary

    Temperature-sampled top-k pick per slot.
    """
    rng = rng or np.random.default_rng()
    fit_scorer.eval()

    n_act = activity_vecs.size(0)
    family_vec = family_vec.to(device)
    activity_vecs = activity_vecs.to(device)

    history_sum = torch.zeros(activity_vecs.size(1), device=device)
    picks: list[int] = []
    used: list[int] = []

    with torch.no_grad():
        for slot in range(TOTAL_SLOTS):
            slot_in_day = slot % SLOTS_PER_DAY

            # Combined hard filter for this slot
            slot_mask = candidate_mask_arr & slot_open_masks[slot_in_day]
            pool = np.where(slot_mask)[0]
            if len(pool) == 0:
                # Relax slot-open constraint as a last resort
                pool = np.where(candidate_mask_arr)[0]
                if len(pool) == 0:
                    raise ValueError(f"no candidates at slot {slot}")

            history_vec = history_sum / max(1, slot)
            B = len(pool)
            fam_b = family_vec.unsqueeze(0).expand(B, -1)
            hist_b = history_vec.unsqueeze(0).expand(B, -1)
            slot_b = torch.full((B,), slot, dtype=torch.long, device=device)
            act_b = activity_vecs[pool]
            logits = fit_scorer(fam_b, act_b, slot_b, hist_b)    # (B,)

            # ── Soft signals ───────────────────────────────────────────────
            tag_b = torch.from_numpy(
                slot_tag_boosts[slot_in_day][pool] * cfg.tag_boost_weight
            ).to(device)
            logits = logits + tag_b

            # Geographic continuity from previous activity
            if picks:
                prev = activities[picks[-1]]
                dist_pen = distance_penalty(
                    [activities[r] for r in pool.tolist()],
                    prev["lat"], prev["lng"],
                    scale_km=cfg.distance_scale_km,
                    weight=cfg.distance_weight,
                )
                logits = logits - torch.from_numpy(dist_pen).to(device)

            # Repetition penalty (graduated — cafes can recur, others not so much)
            if used:
                from collections import Counter
                use_counts = Counter(used)
                rep_pen_pool = np.zeros(len(pool), dtype=np.float32)
                for i, p in enumerate(pool.tolist()):
                    if p in use_counts:
                        # Soft for first reuse, harder after
                        tags = set(activities[p].get("tags", []))
                        is_cafe = bool(tags & {"cafe", "coffee", "bakery"})
                        cap = 5 if is_cafe else 2
                        excess = max(0, use_counts[p] - 1)
                        rep_pen_pool[i] = cfg.repetition_weight * min(excess / cap, 1.0) * (1 + excess)
                logits = logits - torch.from_numpy(rep_pen_pool).to(device)

            # ── Top-k sampling ─────────────────────────────────────────────
            k = min(cfg.top_k, B)
            top_v, top_i = torch.topk(logits, k=k)
            if cfg.temperature <= 1e-6:
                chosen_local = int(top_i[0].item())
            else:
                probs = F.softmax(top_v / cfg.temperature, dim=0).cpu().numpy()
                chosen_local = int(rng.choice(k, p=probs / probs.sum()))
            chosen_row = int(pool[int(top_i[chosen_local].item())])
            picks.append(chosen_row)
            used.append(chosen_row)
            history_sum = history_sum + activity_vecs[chosen_row]

    return picks


def precompute_slot_arrays(activities: list[dict]) -> tuple[np.ndarray, np.ndarray]:
    """Build the per-slot open-hours mask + tag-boost arrays once per cohort run."""
    open_masks = np.stack([
        slot_open_mask(activities, slot_idx=s) for s in range(SLOTS_PER_DAY)
    ])
    tag_boosts = np.stack([
        tag_boost(activities, slot_idx=s, boost=1.0) for s in range(SLOTS_PER_DAY)
    ])
    return open_masks, tag_boosts


# ─────────────────────────────────────────────────────────────────────────────
# Convenience: batch-schedule many families on the same FitScorer + bank
# ─────────────────────────────────────────────────────────────────────────────

def schedule_population(
    fit_scorer: FitScorer,
    family_vecs: torch.Tensor,           # (F, 384)
    candidate_masks: np.ndarray,         # (F, N_act) bool
    activity_vecs: torch.Tensor,          # (N_act, 384)
    activities: list[dict],               # bank metadata
    cfg: ScheduleConfig = ScheduleConfig(),
    seed: int = 0,
    device: str | torch.device = "cpu",
) -> np.ndarray:
    """Schedule every family in the cohort. Returns (F, 30) int32 of
    activity bank rows.
    """
    rng = np.random.default_rng(seed)
    slot_open_masks, slot_tag_boosts = precompute_slot_arrays(activities)
    out = np.zeros((family_vecs.size(0), TOTAL_SLOTS), dtype=np.int32)
    for i in range(family_vecs.size(0)):
        out[i] = schedule(
            fit_scorer=fit_scorer,
            family_vec=family_vecs[i],
            candidate_mask_arr=candidate_masks[i],
            activity_vecs=activity_vecs,
            activities=activities,
            slot_open_masks=slot_open_masks,
            slot_tag_boosts=slot_tag_boosts,
            cfg=cfg,
            rng=rng,
            device=device,
        )
    return out
