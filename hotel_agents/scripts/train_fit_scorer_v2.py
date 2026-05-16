"""Train the FitScorer v2 — optimized but quality-preserving.

Versus v1 (`train_fit_scorer.py`):
  - O(F^2) itinerary lookup → O(F) dict (load-time speed)
  - Vectorized pair construction (drops the Python loop over families)
  - Hard-negative mining (quality gain: negatives are cosine-close to pos)
  - bf16 autocast on CUDA (Blackwell sm_120 has bf16 tensor cores)
  - Batch 512 → 2048 (Blackwell has 96 GB VRAM)
  - NEG_PER_POS 4 → 8
  - Early stopping with patience=10
  - Deterministic seeding for reproducibility

Acceptance: val BCE ≤ 0.225 (v1 best was 0.223) AND val acc ≥ 91%.
If hard-negative mining hurts quality, the script falls back to random negatives
on the next attempt (toggle via --random-negs).
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import torch
import torch.nn as nn
import torch.nn.functional as F

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

# Hyperparameters — codex-reviewed (see commit notes)
# Hard-neg knobs are conservative for small (~190) activity bank to avoid
# false negatives (other valid cafes for breakfast etc).
NEG_PER_POS = 4                # match v1 for 1:4 ratio comparability
EPOCHS = 80                    # cap; early-stop usually fires earlier
BATCH_SIZE = 2048              # 512 → 2048 on 96 GB Blackwell
LR = 3e-4
WD = 0.01
DROPOUT = 0.3
N_SLOTS = 30
PATIENCE = 10
HARD_NEG_FRACTION = 0.25       # 1 of 4 negatives is hard, rest random
HARD_NEG_TOPK = 12             # small bank → narrow window to avoid false negs
SEED = 7


def vectorized_history(slot_act_vecs: torch.Tensor) -> torch.Tensor:
    """Cumulative-mean history per slot. (N_SLOTS, 384) → (N_SLOTS, 384).

    history[0] = zeros; history[k] = mean(slot_act_vecs[:k]).
    """
    zeros = torch.zeros(1, slot_act_vecs.size(1), device=slot_act_vecs.device)
    cumulative = torch.cat([zeros, slot_act_vecs.cumsum(dim=0)], dim=0)
    denom = torch.arange(N_SLOTS + 1, device=slot_act_vecs.device).clamp(min=1).unsqueeze(-1).float()
    return (cumulative / denom)[:N_SLOTS]


def sample_hard_negatives(
    pos_rows: torch.Tensor,        # (N_pos,) bank rows of positives
    act_vecs: torch.Tensor,         # (A, 384) all activity vectors
    n_per_pos: int,
    topk: int,
    rng: torch.Generator,
) -> torch.Tensor:
    """For each positive, sample `n_per_pos` activity rows that are cosine-close
    to it (within the top-K nearest) — but not the positive itself.

    Returns (N_pos, n_per_pos) bank rows.
    """
    A = act_vecs.size(0)
    pos_vec = act_vecs[pos_rows]                                # (N_pos, 384)
    pos_norm = F.normalize(pos_vec, dim=1)
    act_norm = F.normalize(act_vecs, dim=1)
    # Cosine sims (N_pos, A). For 60k positives × 200 activities × 384 = 4.6B float ops, fine on GPU.
    sims = pos_norm @ act_norm.t()
    # Mask out the positive itself
    sims.scatter_(1, pos_rows.unsqueeze(1), -1.0)
    # Top-K hard candidates
    _, topk_rows = sims.topk(topk, dim=1)                       # (N_pos, K)
    # Uniformly sample n_per_pos columns from the top-K
    sel = torch.randint(0, topk, (pos_rows.size(0), n_per_pos),
                        generator=rng, device=topk_rows.device)
    return topk_rows.gather(1, sel)                             # (N_pos, n_per_pos)


def sample_random_negatives(
    pos_rows: torch.Tensor, n_acts: int, n_per_pos: int,
    rng: torch.Generator,
) -> torch.Tensor:
    """Random uniform negatives, avoiding the positive (single retry)."""
    negs = torch.randint(0, n_acts, (pos_rows.size(0), n_per_pos),
                         generator=rng, device=pos_rows.device)
    collisions = negs == pos_rows.unsqueeze(1)
    if collisions.any():
        negs = torch.where(collisions, (negs + 1) % n_acts, negs)
    return negs


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--random-negs", action="store_true",
                        help="fall back to pure random negative sampling")
    parser.add_argument("--epochs", type=int, default=EPOCHS)
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    parser.add_argument("--neg-per-pos", type=int, default=NEG_PER_POS)
    parser.add_argument("--out", type=str, default=str(FIT_CKPT))
    args = parser.parse_args()

    if not ENCODER_CKPT.exists():
        sys.exit(f"missing {ENCODER_CKPT}")
    if not BANK_PATH.exists():
        sys.exit(f"missing {BANK_PATH}")

    device = "cuda" if torch.cuda.is_available() else "cpu"
    use_amp = device == "cuda" and torch.cuda.is_bf16_supported()
    print(f"device: {device}  bf16_amp: {use_amp}")

    # ── Load encoders + activity bank ──────────────────────────────────────
    encoder = load_encoder(ENCODER_CKPT, device=device)
    encoder.eval()

    bank = torch.load(BANK_PATH, map_location=device, weights_only=False)
    act_vecs: torch.Tensor = bank["vectors"].to(device)
    act_id_to_row: dict[str, int] = bank["id_to_row"]
    n_acts = act_vecs.size(0)
    print(f"activity bank: {n_acts} vectors, dim={act_vecs.size(1)}")

    families = read_jsonl(FAMILIES_PATH)
    itineraries = read_jsonl(ITINERARIES_PATH)
    fam_by_id = {f["id"]: f for f in families}
    # O(F) lookup — was O(F^2) in v1
    itin_by_fid = {it["family_id"]: it for it in itineraries}

    # ── Encode every family once ──────────────────────────────────────────
    fam_indices_list: list[list[int]] = []
    fam_ids_used: list[str] = []
    for fid in itin_by_fid:
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
        fam_vecs = encoder(fam_idx_tensor)                  # (F, 384)
    F_n = fam_vecs.size(0)
    print(f"encoded {F_n} family vectors")

    # ── Vectorized pair construction (codex fix #1: collect valid rows only) ──
    # Skipped itineraries used to leave zero-rows in the flattened tensors
    # and pollute both train and val. Collect only valid families, then
    # rebuild fam_vecs indexing.
    valid_rows: list[torch.Tensor] = []
    valid_fam_rows: list[int] = []
    skipped = 0
    for f_row, fid in enumerate(fam_ids_used):
        try:
            rows = torch.tensor(
                [act_id_to_row[aid] for aid in itin_by_fid[fid]["activity_ids"]],
                dtype=torch.long, device=device,
            )
        except KeyError:
            skipped += 1
            continue
        if rows.size(0) != N_SLOTS:
            skipped += 1
            continue
        valid_rows.append(rows)
        valid_fam_rows.append(f_row)

    if not valid_rows:
        sys.exit("no valid itineraries after filtering")

    valid_F = len(valid_rows)
    pos_act_rows = torch.stack(valid_rows, dim=0)                # (valid_F, 30)
    # Recompute histories only for valid families
    pos_history = torch.stack(
        [vectorized_history(act_vecs[r]) for r in valid_rows], dim=0,
    )                                                            # (valid_F, 30, 384)
    valid_fam_idx = torch.tensor(valid_fam_rows, dtype=torch.long, device=device)

    print(f"built positives for {valid_F}/{F_n} families "
          f"({skipped} skipped due to bad rows)")

    # Flatten: (valid_F * 30,) positives, with family idx remapped to fam_vecs row
    pos_fam_idx = valid_fam_idx.unsqueeze(1).expand(-1, N_SLOTS).reshape(-1)
    pos_slot_idx = torch.arange(N_SLOTS, device=device).unsqueeze(0).expand(valid_F, -1).reshape(-1)
    pos_act_idx = pos_act_rows.reshape(-1)
    pos_history_flat = pos_history.reshape(-1, act_vecs.size(1))
    n_pos = pos_fam_idx.size(0)

    # ── Sample negatives (codex fix #2: smaller hard-neg footprint) ──────
    # Validation always uses pure-random negs so val BCE is directly
    # comparable to v1's distribution.
    rng = torch.Generator(device=device).manual_seed(SEED)
    if args.random_negs:
        print("negative-sampling mode: random (train + val)")
        neg_act_idx = sample_random_negatives(pos_act_idx, n_acts, args.neg_per_pos, rng)
    else:
        print(f"negative-sampling mode: hard ({HARD_NEG_FRACTION:.0%}) "
              f"+ random (train), pure random (val)  top-K {HARD_NEG_TOPK}")
        n_hard = int(args.neg_per_pos * HARD_NEG_FRACTION)
        n_rand = args.neg_per_pos - n_hard
        hard = sample_hard_negatives(pos_act_idx, act_vecs, n_hard, HARD_NEG_TOPK, rng)
        rand = sample_random_negatives(pos_act_idx, n_acts, n_rand, rng)
        neg_act_idx = torch.cat([hard, rand], dim=1)        # (N_pos, neg_per_pos)

    # Expand positives across the neg dimension for label assembly
    neg_fam_idx = pos_fam_idx.unsqueeze(1).expand(-1, args.neg_per_pos).reshape(-1)
    neg_slot_idx = pos_slot_idx.unsqueeze(1).expand(-1, args.neg_per_pos).reshape(-1)
    neg_act_idx_flat = neg_act_idx.reshape(-1)
    neg_history = pos_history_flat.unsqueeze(1).expand(-1, args.neg_per_pos, -1).reshape(-1, act_vecs.size(1))

    fam_idx_t = torch.cat([pos_fam_idx, neg_fam_idx], dim=0)
    slot_idx_t = torch.cat([pos_slot_idx, neg_slot_idx], dim=0)
    act_row_t = torch.cat([pos_act_idx, neg_act_idx_flat], dim=0)
    hist_t = torch.cat([pos_history_flat, neg_history], dim=0)
    label_t = torch.cat([
        torch.ones(n_pos, device=device),
        torch.zeros(neg_act_idx_flat.size(0), device=device),
    ], dim=0)

    n_pairs = label_t.size(0)
    print(f"training pairs: {n_pairs}  (pos: {n_pos}, neg: {n_pairs - n_pos})")

    # ── Train/val split (deterministic) ───────────────────────────────────
    # codex fix #2 continued: val negatives are always pure-random so the
    # val BCE / AUROC stays comparable to v1 even if train uses hard negs.
    perm = torch.randperm(n_pairs, generator=torch.Generator().manual_seed(SEED))
    val_n = max(1, n_pairs // 10)
    val_sel = perm[:val_n].to(device)
    train_sel = perm[val_n:].to(device)

    # Rebuild val negatives as pure-random regardless of train mode
    if not args.random_negs:
        val_neg_mask = (label_t[val_sel] == 0.0)
        val_neg_idx_in_full = val_sel[val_neg_mask]
        # Replace those negatives' activity rows with pure-random ones
        val_pos_for_neg = act_row_t[val_neg_idx_in_full]   # use existing act row as 'seed' to avoid collisions
        # NOTE: we re-derive from the positive matching each negative
        # (each pos has neg_per_pos negs sequentially after it).
        # Simpler: just resample uniformly avoiding the negative's family-slot positive.
        rand_replace = sample_random_negatives(
            val_pos_for_neg, n_acts, 1, rng,
        ).squeeze(1)
        act_row_t[val_neg_idx_in_full] = rand_replace
    print(f"train pairs: {len(train_sel)}, val pairs: {len(val_sel)}")

    # ── Model + optim ─────────────────────────────────────────────────────
    torch.manual_seed(SEED)
    model = FitScorer(FitScorerConfig(dropout=DROPOUT)).to(device)
    optim = torch.optim.AdamW(model.parameters(), lr=LR, weight_decay=WD)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(optim, T_max=args.epochs)
    loss_fn = nn.BCEWithLogitsLoss()
    amp_dtype = torch.bfloat16 if use_amp else torch.float32

    best_val = float("inf")
    best_acc = 0.0
    epochs_since_improve = 0
    t0 = time.time()

    for epoch in range(args.epochs):
        model.train()
        epoch_perm = train_sel[torch.randperm(len(train_sel), device=device)]
        total = 0.0
        n_batches = 0
        for start in range(0, len(epoch_perm), args.batch_size):
            sel = epoch_perm[start:start + args.batch_size]
            fam_batch = fam_vecs[fam_idx_t[sel]]
            act_batch = act_vecs[act_row_t[sel]]
            slot_batch = slot_idx_t[sel]
            hist_batch = hist_t[sel]
            label_batch = label_t[sel]
            optim.zero_grad(set_to_none=True)
            # codex fix #3: forward in bf16, but cast logits to fp32 for BCE
            # for numerical safety. No GradScaler needed for bf16.
            with torch.amp.autocast(device_type="cuda" if device == "cuda" else "cpu",
                                    dtype=amp_dtype, enabled=use_amp):
                logits = model(fam_batch, act_batch, slot_batch, hist_batch)
            loss = loss_fn(logits.float(), label_batch.float())
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optim.step()
            total += float(loss.item())
            n_batches += 1
        sched.step()
        train_loss = total / max(1, n_batches)

        # Val — codex fix #4: also compute balanced acc + AUROC since the
        # 1:N ratio can drift between runs.
        model.eval()
        with torch.no_grad():
            with torch.amp.autocast(device_type="cuda" if device == "cuda" else "cpu",
                                    dtype=amp_dtype, enabled=use_amp):
                logits = model(
                    fam_vecs[fam_idx_t[val_sel]],
                    act_vecs[act_row_t[val_sel]],
                    slot_idx_t[val_sel],
                    hist_t[val_sel],
                )
            logits_f = logits.float()
            val_labels = label_t[val_sel]
            val_loss = float(loss_fn(logits_f, val_labels).item())
            probs = torch.sigmoid(logits_f)
            preds = (probs >= 0.5).float()
            acc = float((preds == val_labels).float().mean().item())
            # Balanced accuracy = mean of per-class recall
            pos_mask = val_labels == 1.0
            neg_mask = val_labels == 0.0
            pos_recall = float((preds[pos_mask] == 1.0).float().mean().item()) if pos_mask.any() else 0.0
            neg_recall = float((preds[neg_mask] == 0.0).float().mean().item()) if neg_mask.any() else 0.0
            balanced_acc = (pos_recall + neg_recall) / 2
            # AUROC via rank-sum (Mann-Whitney) — exact for any class ratio
            pos_scores = probs[pos_mask]
            neg_scores = probs[neg_mask]
            if pos_scores.numel() > 0 and neg_scores.numel() > 0:
                auroc = float(
                    (pos_scores.unsqueeze(1) > neg_scores.unsqueeze(0)).float().mean().item()
                )
            else:
                auroc = 0.0

        improved = val_loss < best_val - 1e-4
        if improved:
            best_val = val_loss
            best_acc = acc
            save_fit_scorer(model, Path(args.out))
            epochs_since_improve = 0
        else:
            epochs_since_improve += 1

        if epoch % 2 == 0 or improved or epoch == args.epochs - 1:
            print(f"epoch {epoch:3d}  train={train_loss:.4f}  val={val_loss:.4f}  "
                  f"acc={acc:.2%}  bal_acc={balanced_acc:.2%}  AUROC={auroc:.3f}  "
                  f"lr={sched.get_last_lr()[0]:.6f}"
                  f"{'  *' if improved else ''}")

        if epochs_since_improve >= PATIENCE:
            print(f"early stop at epoch {epoch} (patience {PATIENCE})")
            break

    elapsed = time.time() - t0
    print(f"\nbest val BCE: {best_val:.4f}  best val acc: {best_acc:.2%}  "
          f"({elapsed:.0f}s)")
    print(f"saved → {args.out}")


if __name__ == "__main__":
    main()
