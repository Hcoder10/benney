"""Train the FitScorer on (family, activity, slot, history) → fit triples
extracted from the synthetic itineraries.

Training data construction:
  For each family's 30-slot itinerary:
    For each slot k in 0..29:
      pos = (family_vec, activity_vec[itinerary[k]], k, mean(act_vecs[0:k]))  →  1.0
      For each of K negatives:
        neg_aid = sample uniformly from activity_bank \ {itinerary[k]}
        neg = (family_vec, activity_vec[neg_aid], k, history)                 →  0.0

With ~150 families × 30 slots × 5 (1 pos + 4 neg) = 22,500 training pairs.

Loss: BCEWithLogitsLoss
Optim: AdamW lr=3e-4 wd=0.01, cosine schedule
Output: checkpoints/fit_scorer.pt
"""

from __future__ import annotations

import sys
from pathlib import Path

import torch
import torch.nn as nn

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from hotel_agents.shared.encoder import (  # noqa: E402
    family_to_indices,
    load_encoder,
)
from hotel_agents.shared.storage import (  # noqa: E402
    CHECKPOINTS_DIR,
    FAMILIES_PATH,
    ITINERARIES_PATH,
    read_jsonl,
)
from hotel_agents.trip_planner.fit_scorer import (  # noqa: E402
    FitScorer,
    FitScorerConfig,
    save_fit_scorer,
)

ENCODER_CKPT = CHECKPOINTS_DIR / "family_encoder.pt"
BANK_PATH = CHECKPOINTS_DIR / "activity_bank.pt"
FIT_CKPT = CHECKPOINTS_DIR / "fit_scorer.pt"

NEG_PER_POS = 4
EPOCHS = 60
BATCH_SIZE = 512
LR = 3e-4
WD = 0.01
DROPOUT = 0.3
N_SLOTS = 30


def main() -> None:
    if not ENCODER_CKPT.exists():
        sys.exit(f"missing {ENCODER_CKPT} — run train_family_encoder.py first")
    if not BANK_PATH.exists():
        sys.exit(f"missing {BANK_PATH} — run build_activity_bank.py first")

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"device: {device}")

    # ── Load encoders + activity bank ──────────────────────────────────────
    encoder = load_encoder(ENCODER_CKPT, device=device)
    encoder.eval()

    bank = torch.load(BANK_PATH, map_location=device, weights_only=False)
    act_vecs: torch.Tensor = bank["vectors"].to(device)         # (A, 384)
    act_id_to_row: dict[str, int] = bank["id_to_row"]
    n_acts = act_vecs.size(0)
    print(f"activity bank: {n_acts} vectors")

    families = read_jsonl(FAMILIES_PATH)
    itineraries = read_jsonl(ITINERARIES_PATH)
    fam_by_id = {f["id"]: f for f in families}

    # ── Encode every family once ──────────────────────────────────────────
    fam_indices_list: list[list[int]] = []
    fam_ids_used: list[str] = []
    for it in itineraries:
        fid = it["family_id"]
        if fid not in fam_by_id:
            continue
        try:
            fam_indices_list.append(family_to_indices(fam_by_id[fid]))
        except ValueError:
            continue
        fam_ids_used.append(fid)
    if not fam_ids_used:
        sys.exit("no usable families")

    fam_idx_tensor = torch.tensor(fam_indices_list, dtype=torch.long, device=device)
    with torch.no_grad():
        fam_vecs = encoder(fam_idx_tensor)                        # (F, 384)
    print(f"encoded {fam_vecs.size(0)} family vectors")

    # ── Build training tensors ─────────────────────────────────────────────
    # Pre-compute history vectors per (family, slot) as mean of previous slots' vecs.
    # Then explode into (positive, negative) pairs.
    family_idx_per_pair: list[int] = []
    slot_idx_per_pair: list[int] = []
    activity_row_per_pair: list[int] = []
    history_vecs_per_pair: list[torch.Tensor] = []
    label_per_pair: list[float] = []

    valid_itin_count = 0
    for f_row, fid in enumerate(fam_ids_used):
        # Find the matching itinerary
        it = next((it for it in itineraries if it["family_id"] == fid), None)
        if it is None:
            continue
        activity_ids = it["activity_ids"]
        # Rows in activity bank for this family's itinerary
        rows = [act_id_to_row[aid] for aid in activity_ids]
        slot_act_vecs = act_vecs[rows]            # (30, 384)
        # Cumulative mean for history_vec[k] = mean(slot_act_vecs[:k])
        # history[0] = zeros; history[k] = mean of first k vectors
        zeros = torch.zeros(1, slot_act_vecs.size(1), device=device)
        cumulative = torch.cat([zeros, slot_act_vecs.cumsum(dim=0)], dim=0)  # (31, 384)
        denom = torch.arange(N_SLOTS + 1, device=device).clamp(min=1).unsqueeze(-1).float()
        history_per_slot = cumulative / denom     # (31, 384); use [0..29]
        for slot in range(N_SLOTS):
            pos_row = rows[slot]
            family_idx_per_pair.append(f_row)
            slot_idx_per_pair.append(slot)
            activity_row_per_pair.append(pos_row)
            history_vecs_per_pair.append(history_per_slot[slot])
            label_per_pair.append(1.0)
            # Negatives: random activities ≠ pos_row
            negs = torch.randint(0, n_acts, (NEG_PER_POS,), device=device)
            negs = torch.where(negs == pos_row, (negs + 1) % n_acts, negs)
            for n in negs.tolist():
                family_idx_per_pair.append(f_row)
                slot_idx_per_pair.append(slot)
                activity_row_per_pair.append(n)
                history_vecs_per_pair.append(history_per_slot[slot])
                label_per_pair.append(0.0)
        valid_itin_count += 1

    n_pairs = len(label_per_pair)
    print(f"training pairs: {n_pairs} from {valid_itin_count} itineraries "
          f"(pos: {label_per_pair.count(1.0)}, neg: {label_per_pair.count(0.0)})")

    fam_idx_t = torch.tensor(family_idx_per_pair, dtype=torch.long, device=device)
    slot_idx_t = torch.tensor(slot_idx_per_pair, dtype=torch.long, device=device)
    act_row_t = torch.tensor(activity_row_per_pair, dtype=torch.long, device=device)
    hist_t = torch.stack(history_vecs_per_pair)
    label_t = torch.tensor(label_per_pair, dtype=torch.float32, device=device)

    # ── Train/val split (deterministic) ─────────────────────────────────────
    perm = torch.randperm(n_pairs, generator=torch.Generator().manual_seed(7))
    val_n = max(1, n_pairs // 10)
    val_sel, train_sel = perm[:val_n], perm[val_n:]
    print(f"train pairs: {len(train_sel)}, val pairs: {len(val_sel)}")

    # ── Model + optim ───────────────────────────────────────────────────────
    model = FitScorer(FitScorerConfig(dropout=DROPOUT)).to(device)
    optim = torch.optim.AdamW(model.parameters(), lr=LR, weight_decay=WD)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(optim, T_max=EPOCHS)
    loss_fn = nn.BCEWithLogitsLoss()

    best_val = float("inf")
    for epoch in range(EPOCHS):
        model.train()
        epoch_perm = train_sel[torch.randperm(len(train_sel))]
        total = 0.0
        n_batches = 0
        for start in range(0, len(epoch_perm), BATCH_SIZE):
            sel = epoch_perm[start:start + BATCH_SIZE]
            fam_batch = fam_vecs[fam_idx_t[sel]]
            act_batch = act_vecs[act_row_t[sel]]
            slot_batch = slot_idx_t[sel]
            hist_batch = hist_t[sel]
            label_batch = label_t[sel]
            logits = model(fam_batch, act_batch, slot_batch, hist_batch)
            loss = loss_fn(logits, label_batch)
            optim.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optim.step()
            total += float(loss.item())
            n_batches += 1
        sched.step()
        train_loss = total / max(1, n_batches)

        # Val
        model.eval()
        with torch.no_grad():
            fam_b = fam_vecs[fam_idx_t[val_sel]]
            act_b = act_vecs[act_row_t[val_sel]]
            slot_b = slot_idx_t[val_sel]
            hist_b = hist_t[val_sel]
            label_b = label_t[val_sel]
            logits = model(fam_b, act_b, slot_b, hist_b)
            val_loss = float(loss_fn(logits, label_b).item())
            # accuracy + AUROC-lite
            preds = (torch.sigmoid(logits) >= 0.5).float()
            acc = float((preds == label_b).float().mean().item())
        if val_loss < best_val:
            best_val = val_loss
            save_fit_scorer(model, FIT_CKPT)
        if epoch % 5 == 0 or epoch == EPOCHS - 1:
            print(f"epoch {epoch:3d}  train={train_loss:.4f}  val={val_loss:.4f}  "
                  f"val_acc={acc:.2%}  lr={sched.get_last_lr()[0]:.6f}")

    print(f"\nbest val BCE: {best_val:.4f}")
    print(f"saved → {FIT_CKPT}")


if __name__ == "__main__":
    main()
