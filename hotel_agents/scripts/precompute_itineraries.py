"""Run the scheduler across the full synthetic cohort to produce
itineraries_50k.npy — the (F, 30) int32 matrix tallied at query time.

For the hackathon we have ~200-300 seed families. The plan calls for
augmenting to 50k via latent-space interpolation, which is a separate
script (precompute_itineraries.py here only handles the explicit cohort
we generated; interpolation lives in scripts/augment_cohort.py).

The temperature is the key knob — too low and every family picks the
same activity (probability bars collapse to "100% chose X"); too high
and the cohort is noise. Default 0.6, override via --temperature.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from hotel_agents.shared.encoder import (  # noqa: E402
    family_to_indices,
    load_encoder,
)
from hotel_agents.shared.storage import (  # noqa: E402
    ACTIVITIES_PATH,
    CHECKPOINTS_DIR,
    DATA_DIR,
    FAMILIES_PATH,
    read_json,
    read_jsonl,
)
from hotel_agents.trip_planner.fit_scorer import load_fit_scorer  # noqa: E402
from hotel_agents.trip_planner.scheduler import (  # noqa: E402
    ScheduleConfig,
    candidate_mask,
    schedule_population,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--temperature", type=float, default=0.6)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", type=str, default=str(DATA_DIR / "itineraries_cohort.npz"))
    args = parser.parse_args()

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"device: {device}")

    # ── Load everything ────────────────────────────────────────────────────
    encoder = load_encoder(CHECKPOINTS_DIR / "family_encoder.pt", device=device)
    fit_scorer = load_fit_scorer(CHECKPOINTS_DIR / "fit_scorer.pt", device=device)
    bank = torch.load(CHECKPOINTS_DIR / "activity_bank.pt", map_location=device, weights_only=False)
    activity_vecs: torch.Tensor = bank["vectors"].to(device)
    activity_ids: list[str] = bank["ids"]

    activities = read_json(ACTIVITIES_PATH)
    # Reorder activities to match bank row order (defensive)
    id_to_act = {a["id"]: a for a in activities}
    activities_in_bank_order = [id_to_act[aid] for aid in activity_ids]

    families = read_jsonl(FAMILIES_PATH)
    print(f"cohort: {len(families)} families, {len(activities)} activities")

    # ── Encode families + build constraint masks ──────────────────────────
    valid_families: list[dict] = []
    fam_indices: list[list[int]] = []
    masks: list[np.ndarray] = []
    for fam in families:
        try:
            fam_indices.append(family_to_indices(fam))
        except ValueError as e:
            print(f"  skip {fam['id']}: {e}")
            continue
        masks.append(candidate_mask(activities_in_bank_order, fam))
        valid_families.append(fam)

    idx_tensor = torch.tensor(fam_indices, dtype=torch.long, device=device)
    with torch.no_grad():
        family_vecs = encoder(idx_tensor)                # (F, 384)
    masks_arr = np.stack(masks)                          # (F, N_act)

    print(f"encoded {family_vecs.size(0)} family vectors, "
          f"avg candidates/family: {masks_arr.sum(axis=1).mean():.0f}")

    # ── Run scheduler ──────────────────────────────────────────────────────
    cfg = ScheduleConfig(temperature=args.temperature, top_k=args.top_k)
    print(f"scheduling: T={cfg.temperature}, top_k={cfg.top_k}")
    itineraries = schedule_population(
        fit_scorer=fit_scorer,
        family_vecs=family_vecs,
        candidate_masks=masks_arr,
        activity_vecs=activity_vecs,
        activities=activities_in_bank_order,
        cfg=cfg,
        seed=args.seed,
        device=device,
    )                                                     # (F, 30) int32
    print(f"produced itineraries: shape={itineraries.shape}")

    # ── Save ───────────────────────────────────────────────────────────────
    family_ids = [f["id"] for f in valid_families]
    out_path = Path(args.out)
    np.savez(
        out_path,
        itineraries=itineraries,
        family_ids=np.array(family_ids),
        activity_ids=np.array(activity_ids),
        family_vecs=family_vecs.cpu().numpy(),
    )
    print(f"saved → {out_path}")

    # ── Quick sanity: distribution of slot-0 picks ────────────────────────
    from collections import Counter
    slot0 = Counter(itineraries[:, 0].tolist())
    top5 = slot0.most_common(5)
    print("\nslot-0 picks (top 5):")
    for row, n in top5:
        print(f"  {n:>3}× ({n / len(itineraries):>5.1%})  {activity_ids[row]}")
    print(f"\nslot-0 unique activities chosen: {len(slot0)} / {len(activity_ids)} in bank")


if __name__ == "__main__":
    main()
