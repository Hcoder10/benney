"""Haiku-narrated action cards.

The predictor outputs numbers; the narrator turns one number-soup into a
short imperative line a real housekeeper / room-service runner can act on.

We cache by (room, type, deadline_bucket, urgency) so the same situation
across polls doesn't re-bill the API. Cache TTL: 90 s.
"""

from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime
from typing import Optional

from anthropic import AsyncAnthropic

from .schema import ActionCard, Prediction, RoomState

_CLIENT: Optional[AsyncAnthropic] = None
_CACHE: dict[tuple, tuple[float, ActionCard]] = {}
_TTL_S = 90.0
MODEL = "claude-haiku-4-5-20251001"


def _client() -> Optional[AsyncAnthropic]:
    global _CLIENT
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key or not key.startswith("sk-ant-"):
        return None
    if _CLIENT is None:
        _CLIENT = AsyncAnthropic(api_key=key)
    return _CLIENT


def _hhmm(ts: float | None) -> str:
    if ts is None:
        return "-"
    return datetime.fromtimestamp(ts).strftime("%-I:%M %p") \
        if os.name != "nt" else datetime.fromtimestamp(ts).strftime("%#I:%M %p")


def _minutes_until(ts: float | None, now: float) -> str:
    if ts is None:
        return "-"
    delta = int((ts - now) / 60)
    if delta <= 0:
        return f"{-delta} min ago"
    if delta < 60:
        return f"in {delta} min"
    return f"in {delta // 60}h {delta % 60}m"


def _urgency(deadline_ts: float | None, now_ts: float) -> str:
    if deadline_ts is None:
        return "info"
    delta_min = (deadline_ts - now_ts) / 60
    if delta_min <= 5:
        return "now"
    if delta_min <= 30:
        return "soon"
    return "info"


# ─────────────────────────────────────────────────────────────────────────────
# Per-type fact lines (these go straight to the UI even with no Haiku).
# Haiku rewrites these into more natural English when available.
# ─────────────────────────────────────────────────────────────────────────────

def _housekeeping_facts(pred: Prediction, fam_summary: str) -> tuple[str, str, str]:
    """Return (action_line, reasoning, deadline_kind)."""
    if pred.in_room:
        line = f"Hold - guest in room."
        reason = "Door last opened; do not enter. We'll page when they leave."
        return line, reason, "hold"
    rec = pred.cleaning_recommendation
    win = pred.cleaning_window_min or 0
    ret_h = _hhmm(pred.expected_return_ts)
    if rec == "skip":
        line = f"Skip turn - guest back {ret_h}."
        reason = f"Only {win} min available; not enough for safe turn."
        return line, reason, "skip"
    if rec == "quick-turn":
        line = f"Quick turn now - {win} min before return at {ret_h}."
        reason = (f"Linens + bath only. Guest is {fam_summary}, "
                  f"due back {ret_h} from their next slot.")
        return line, reason, "quick"
    line = f"Full service OK - {win} min budget, guest back {ret_h}."
    reason = (f"Plenty of time for a complete turn. "
              f"Guest is {fam_summary}.")
    return line, reason, "full"


def _room_service_facts(pred: Prediction, fam_summary: str) -> tuple[str, str, str]:
    cook_h = _hhmm(pred.food_cook_start_ts)
    deliver_h = _hhmm(pred.food_deliver_ts)
    cook_in = _minutes_until(pred.food_cook_start_ts, pred.now_ts)
    line = f"Start cooking {cook_h} ({cook_in}). Deliver {deliver_h}."
    reason = (f"Back-scheduled from guest return so food hits the door hot. "
              f"Guest is {fam_summary}.")
    return line, reason, "cook"


def _arrival_facts(pred: Prediction, fam_summary: str,
                   flight_info: dict | None) -> tuple[str, str, str]:
    arr_h = _hhmm(pred.arrival_eta_ts)
    setup_h = _hhmm(pred.arrival_setup_ts)
    setup_in = _minutes_until(pred.arrival_setup_ts, pred.now_ts)
    fl = flight_info or {}
    flight_blurb = ""
    if fl.get("flight"):
        delay = fl.get("delay_min", 0)
        flight_blurb = f"Flight {fl['flight']}"
        if delay:
            flight_blurb += f" delayed {delay} min"
        flight_blurb += f" lands {_hhmm(fl.get('estimated_arr_ts'))}; "
    line = f"Welcome ready by {setup_h} ({setup_in}). Guest swipes in {arr_h}."
    reason = (f"{flight_blurb}fresh fruit + handwritten card + staff greet. "
              f"Guest is {fam_summary}.")
    return line, reason, "welcome"


# ─────────────────────────────────────────────────────────────────────────────
# Public: produce a card per active concern
# ─────────────────────────────────────────────────────────────────────────────

async def narrate(
    state: RoomState,
    pred: Prediction,
    fam_summary: str,
    flight_info: dict | None = None,
) -> list[ActionCard]:
    """Return zero or more cards for this room.

    Order matters for the staff board; we sort by urgency client-side.
    """
    cards: list[ActionCard] = []

    # Housekeeping: every checked-in room gets one, unless mid-checkout
    if state.family_id:
        line, reason, _ = _housekeeping_facts(pred, fam_summary)
        deadline = pred.expected_return_ts if pred.cleaning_recommendation in ("quick-turn", "go-now") else None
        cards.append(ActionCard(
            room=state.room, type="housekeeping",
            urgency=_urgency(deadline, pred.now_ts),  # type: ignore[arg-type]
            action_line=line, reasoning=reason, deadline_ts=deadline,
        ))

    # Room service: only if there's a pending order
    if state.pending_food_order:
        line, reason, _ = _room_service_facts(pred, fam_summary)
        cards.append(ActionCard(
            room=state.room, type="room_service",
            urgency=_urgency(pred.food_cook_start_ts, pred.now_ts),  # type: ignore[arg-type]
            action_line=line, reasoning=reason,
            deadline_ts=pred.food_cook_start_ts,
        ))

    # Arrival: only if guest hasn't arrived yet
    if state.arrival_eta_ts and not state.in_room and not state.family_id:
        line, reason, _ = _arrival_facts(pred, fam_summary, flight_info)
        cards.append(ActionCard(
            room=state.room, type="arrival",
            urgency=_urgency(pred.arrival_setup_ts, pred.now_ts),  # type: ignore[arg-type]
            action_line=line, reasoning=reason,
            deadline_ts=pred.arrival_setup_ts,
        ))

    # Optionally re-write the action_line via Haiku for naturalness.
    client = _client()
    if client is None or not cards:
        return cards
    rewritten = await asyncio.gather(
        *[_haiku_rewrite(client, c, fam_summary) for c in cards],
        return_exceptions=True,
    )
    out: list[ActionCard] = []
    for card, r in zip(cards, rewritten):
        if isinstance(r, ActionCard):
            out.append(r)
        else:
            out.append(card)        # fall back to fact-line on any API error
    return out


async def _haiku_rewrite(client: AsyncAnthropic, card: ActionCard,
                         fam_summary: str) -> ActionCard:
    key = (card.room, card.type, int(card.deadline_ts or 0) // 60, card.urgency)
    now = time.time()
    cached = _CACHE.get(key)
    if cached and now - cached[0] < _TTL_S:
        return cached[1]

    prompt = (
        f"You write 1-line action cards for hotel staff. The card type is "
        f"`{card.type}`. The room is {card.room}. The guest is: {fam_summary}.\n\n"
        f"Raw facts: {card.action_line}\n"
        f"Context: {card.reasoning}\n\n"
        f"Rewrite ONLY the action line as one imperative sentence ≤ 14 words. "
        f"Keep all times and numbers exact. No emojis. Just the sentence."
    )

    msg = await client.messages.create(
        model=MODEL,
        max_tokens=80,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
    if not text:
        return card
    rewritten = ActionCard(
        room=card.room, type=card.type, urgency=card.urgency,
        action_line=text, reasoning=card.reasoning, deadline_ts=card.deadline_ts,
    )
    _CACHE[key] = (now, rewritten)
    return rewritten
