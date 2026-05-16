"""Train the FamilyEncoder against archetype-text targets.

Pipeline:
  families.jsonl    →  family_to_indices  →  (N, 15) int64
  anchors.jsonl     →  archetype_id per family
  archetypes.json   →  description per archetype
                        ↓ sentence-transformers
                       (N_arch, 384) cached target vectors
                        ↓ index by family's anchor
                        target (N, 384)

  Loss: 1 - cosine(FamilyEncoder(indices), target)
  Optim: AdamW, cosine schedule, ~150 epochs
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import torch
import torch.nn.functional as F

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from hotel_agents.shared.embedding import cached_encode  # noqa: E402
from hotel_agents.shared.encoder import (  # noqa: E402
    EncoderConfig,
    FamilyEncoder,
    cosine_loss,
    family_to_indices,
    save_encoder,
)
from hotel_agents.shared.storage import (  # noqa: E402
    ANCHORS_PATH,
    ARCHETYPES_PATH,
    CHECKPOINTS_DIR,
    DATA_DIR,
    FAMILIES_PATH,
    read_json,
    read_jsonl,
)

CKPT_PATH = CHECKPOINTS_DIR / "family_encoder.pt"
EMB_CACHE = DATA_DIR / "archetype_embeddings.pt"


def main() -> None:
    # ── Load data ───────────────────────────────────────────────────────────
    families = read_jsonl(FAMILIES_PATH)
    anchors_list = read_jsonl(ANCHORS_PATH)
    archetypes = read_json(ARCHETYPES_PATH)
    if not families:
        sys.exit("no families found; run wave-gen + merge_families first")

    print(f"loaded {len(families)} families, {len(archetypes)} archetypes")

    # ── Embed archetype descriptions (cached) ───────────────────────────────
    arch_descs = [
        f"{a['name']}: {a.get('description', '')}"
        for a in archetypes
    ]
    arch_vecs = cached_encode(arch_descs, EMB_CACHE)
    arch_id_to_row = {a["id"]: i for i, a in enumerate(archetypes)}

    # ── Map families → indices + targets ────────────────────────────────────
    anchor_by_fid = {a["family_id"]: a["archetype_id"] for a in anchors_list}
    X_rows: list[list[int]] = []
    Y_rows: list[torch.Tensor] = []
    skipped = 0
    for fam in families:
        fid = fam["id"]
        anchor = anchor_by_fid.get(fid)
        if anchor not in arch_id_to_row:
            skipped += 1
            continue
        try:
            X_rows.append(family_to_indices(fam))
        except ValueError as e:
            print(f"  skip {fid}: {e}")
            skipped += 1
            continue
        Y_rows.append(arch_vecs[arch_id_to_row[anchor]])

    if not X_rows:
        sys.exit("no valid (family, archetype) training pairs")
    X = torch.tensor(X_rows, dtype=torch.long)
    Y = torch.stack(Y_rows)
    print(f"training pairs: {len(X_rows)} (skipped {skipped})")

    # ── Train/val split (deterministic) ─────────────────────────────────────
    n = len(X)
    perm = torch.randperm(n, generator=torch.Generator().manual_seed(42))
    val_n = max(1, n // 8)
    val_idx, train_idx = perm[:val_n], perm[val_n:]
    X_train, Y_train = X[train_idx], Y[train_idx]
    X_val, Y_val = X[val_idx], Y[val_idx]
    print(f"train={len(X_train)}, val={len(X_val)}")

    # ── Model + optim ───────────────────────────────────────────────────────
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"device: {device}")
    model = FamilyEncoder(EncoderConfig()).to(device)
    X_train, Y_train = X_train.to(device), Y_train.to(device)
    X_val, Y_val = X_val.to(device), Y_val.to(device)

    epochs = 150
    batch_size = min(32, len(X_train))
    optim = torch.optim.AdamW(model.parameters(), lr=3e-3, weight_decay=0.01)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(optim, T_max=epochs)

    best_val = float("inf")
    for epoch in range(epochs):
        model.train()
        perm = torch.randperm(len(X_train))
        total = 0.0
        n_batches = 0
        for start in range(0, len(X_train), batch_size):
            idx = perm[start:start + batch_size]
            xb, yb = X_train[idx], Y_train[idx]
            pred = model(xb)
            loss = cosine_loss(pred, yb)
            optim.zero_grad()
            loss.backward()
            optim.step()
            total += float(loss.item())
            n_batches += 1
        sched.step()
        train_loss = total / max(1, n_batches)

        # ── Val ────────────────────────────────────────────────────────────
        model.eval()
        with torch.no_grad():
            val_pred = model(X_val)
            val_loss = float(cosine_loss(val_pred, Y_val).item())
            # Top-k retrieval accuracy: for each val family, does its anchor
            # archetype rank in the top-3 nearest archetypes by cosine?
            pred_n = F.normalize(val_pred, dim=-1)
            arch_n = F.normalize(arch_vecs.to(device), dim=-1)
            sims = pred_n @ arch_n.T                            # (V, A)
            # build a label tensor mapping val rows → archetype row index
            val_archetype_idx = torch.tensor(
                [arch_id_to_row[anchor_by_fid[families[i]["id"]]]
                 for i in val_idx.tolist()
                 if anchor_by_fid.get(families[i]["id"]) in arch_id_to_row],
                device=device,
            )
            top3 = sims.topk(3, dim=-1).indices                  # (V, 3)
            in_top3 = (top3 == val_archetype_idx.unsqueeze(1)).any(dim=-1).float().mean().item()

        if val_loss < best_val:
            best_val = val_loss
            CKPT_PATH.parent.mkdir(parents=True, exist_ok=True)
            save_encoder(model, CKPT_PATH)
        if epoch % 10 == 0 or epoch == epochs - 1:
            print(f"epoch {epoch:3d}  train={train_loss:.4f}  val={val_loss:.4f}  top3={in_top3:.2%}  lr={sched.get_last_lr()[0]:.6f}")

    print(f"\nbest val cosine loss: {best_val:.4f}")
    print(f"saved → {CKPT_PATH}")


if __name__ == "__main__":
    main()
