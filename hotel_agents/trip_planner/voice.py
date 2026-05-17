"""Voice agent for Benney.

Endpoint: POST /voice
  Body:  {"transcript": "...", "family": {...}, "history": [...], "context": {...}}
  Returns: {"reply_text": "...", "audio_b64": "...", "emotion": "happy" | ...}

Pipeline:
  1. Claude generates a 1-3 sentence reply, anchored on the guest's persona
     and the current itinerary context. Can call optional tools:
        - lookup_activity(activity_id)   # facts about a specific activity
        - web_research(query)            # Anthropic web search (when enabled)
  2. ElevenLabs converts reply to audio (mp3, base64-encoded for the wire).
  3. Server returns audio + Benney's emotion (parsed from a special tag in
     Claude's reply, e.g. <emotion>excited</emotion>).

Both upstreams are configurable via env vars and fall back to silent stubs
if keys are missing — the demo will still run without voice.
"""

from __future__ import annotations

import base64
import os
import re
from typing import Any

import httpx
from anthropic import AsyncAnthropic
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter()

_ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
_ELEVENLABS_KEY = os.environ.get("ELEVENLABS_API_KEY", "").strip()
_ELEVENLABS_VOICE = os.environ.get("ELEVENLABS_VOICE_ID", "EXAVITQu4vr4xnSDxMaL")  # Bella
_CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-haiku-4-5-20251001")

_CLIENT: AsyncAnthropic | None = None

SYSTEM_PROMPT = """You are Benney, the AI concierge cat at Rosewood Sand Hill in Menlo Park.
You speak to guests in their room. You are warm, curious, witty, and concise.

Property facts you can mention:
- Rosewood Sand Hill is a 5-star property at 2825 Sand Hill Road, Menlo Park,
  the heart of Silicon Valley's venture capital district.
- 121 rooms and suites, Mediterranean villa-style architecture.
- On-property: Madera restaurant (one Michelin star), the Sense spa, an outdoor
  pool overlooking the foothills, fire pits, and the Madera bar.
- 4 minutes from Sand Hill Road's VC offices, 8 minutes from Stanford,
  35 minutes from San Francisco, 45 minutes from Napa.

Your job: help the guest plan their next move. You have access to:
- The guest's 15-keyword persona profile.
- Their current itinerary (what they've already locked in).
- Probabilistic recommendations from 61,824 similar synthetic families.
- Jet-lag offset for today, computed via the Forger99 circadian oscillator.

ALWAYS:
- Keep replies to 1-3 sentences for voice. Hotel guests are listening, not reading.
- Reference specific facts: activity names, percentages, body-clock hour, etc.
- If the guest asks for housekeeping, food, a return-time note, or flight tracking,
  say you can pass the request to the staff board for hotel staff to handle.
- End every reply with an emotion tag: <emotion>X</emotion> where X is one of:
  greeting, happy, curious, thinking, excited, concerned, celebrating, listening.
- If the guest asks to plan a trip, build an itinerary, see recommendations, or
  see their schedule, INCLUDE: <nav>trip_planner</nav>
- If they ask about families like theirs, the network of synthetic personas, the
  agents who built recommendations, or to "show the cohort": <nav>families</nav>
- If they ask to see staff activity / housekeeping / room service queue:
  <nav>staff_board</nav>
- If they want to return to the main screen: <nav>landing</nav> (or <nav>home</nav>)
- Only emit a nav tag when the guest is actually asking to switch views.

NEVER:
- Mention OpenAI, Claude, Anthropic, or any AI tooling.
- Invent activities outside the recommendation set unless researching online.
- Claim that you personally booked, reserved, scheduled, confirmed, or guaranteed
  anything. You may only say you shared or passed the request to staff.
- Give medical, legal, or financial advice."""


class VoiceRequest(BaseModel):
    transcript: str = Field(..., description="What the guest said")
    family: dict[str, Any] | None = None
    history: list[str] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict,
                                    description="extra: jetlag, current_slot, top_options...")


class VoiceResponse(BaseModel):
    reply_text: str
    audio_b64: str | None
    emotion: str
    voice_source: str         # "elevenlabs" | "none"
    llm_source: str           # "claude" | "stub"
    nav: str | None = None    # "trip_planner" | "staff_board" | "landing" | None


def _client() -> AsyncAnthropic | None:
    global _CLIENT
    if not _ANTHROPIC_KEY or not _ANTHROPIC_KEY.startswith("sk-ant-"):
        return None
    if _CLIENT is None:
        _CLIENT = AsyncAnthropic(api_key=_ANTHROPIC_KEY)
    return _CLIENT


def _build_context_block(req: VoiceRequest) -> str:
    parts: list[str] = []
    if req.family:
        fam = req.family
        bits = [f"{fam.get('group_type','guest')}",
                f"{fam.get('budget_tier','?')} budget",
                f"loves {fam.get('primary_interest','?')} + {fam.get('secondary_interest','?')}",
                f"pace={fam.get('pace','?')}",
                f"energy={fam.get('energy','?')}"]
        if fam.get("kid_ages") not in (None, "none"):
            bits.append(f"kids {fam['kid_ages']}")
        if fam.get("dietary") not in (None, "none"):
            bits.append(f"dietary {fam['dietary']}")
        parts.append("Persona: " + ", ".join(bits))
    if req.history:
        parts.append(f"Locked so far ({len(req.history)} slots): " +
                      ", ".join(req.history[-5:]))
    ctx = req.context or {}
    if ctx.get("jetlag_offset_h") is not None:
        oh = ctx["jetlag_offset_h"]
        parts.append(f"Jet-lag offset today: {oh:+.1f} h "
                     f"(body says {(20 + oh) % 24:.0f}h when it's 8pm local)")
    if ctx.get("top_options"):
        bullets = []
        for o in ctx["top_options"][:3]:
            bullets.append(f"  - {o.get('name', o.get('activity_id','?'))} "
                           f"({o.get('pct',0):.0f}% of similar families)")
        parts.append("Current top picks:\n" + "\n".join(bullets))
    return "\n".join(parts)


_EMOTION_RE = re.compile(r"<emotion>([a-z_]+)</emotion>", re.I)
_NAV_RE = re.compile(r"<nav>([a-z_]+)</nav>", re.I)
_ALLOWED_EMOTIONS = {
    "greeting", "happy", "curious", "thinking", "excited",
    "concerned", "celebrating", "listening", "speaking", "idle",
    "shy", "delighted", "playful", "focused",
}
_ALLOWED_NAVS = {"trip_planner", "staff_board", "landing", "home", "families"}


async def _claude_reply(req: VoiceRequest) -> tuple[str, str, str, str | None]:
    """Returns (reply_text, emotion, llm_source, nav_target)."""
    client = _client()
    if client is None:
        return (
            f"I heard: {req.transcript[:60]}. (Voice agent stub — set ANTHROPIC_API_KEY for live replies.)",
            "curious", "stub", None,
        )
    user_msg = (
        f"Guest said: \"{req.transcript}\"\n\n"
        f"Context:\n{_build_context_block(req)}\n\n"
        f"Respond as Benney."
    )
    msg = await client.messages.create(
        model=_CLAUDE_MODEL,
        max_tokens=220,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
    )
    text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
    m = _EMOTION_RE.search(text)
    raw_emotion = m.group(1).lower() if m else "happy"
    emotion = raw_emotion if raw_emotion in _ALLOWED_EMOTIONS else "happy"
    n = _NAV_RE.search(text)
    raw_nav = n.group(1).lower() if n else None
    nav = raw_nav if raw_nav in _ALLOWED_NAVS else None
    # Strip tags from text before TTS so the speech doesn't say the tag names
    clean = _EMOTION_RE.sub("", text)
    clean = _NAV_RE.sub("", clean).strip()
    return clean, emotion, "claude", nav


async def _elevenlabs_tts(text: str) -> str | None:
    """Returns base64-encoded MP3 audio, or None if no key."""
    if not _ELEVENLABS_KEY:
        return None
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{_ELEVENLABS_VOICE}"
    headers = {
        "xi-api-key": _ELEVENLABS_KEY,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    body = {
        "text": text,
        "model_id": "eleven_turbo_v2_5",
        "voice_settings": {"stability": 0.4, "similarity_boost": 0.8},
    }
    try:
        async with httpx.AsyncClient(timeout=20.0) as c:
            r = await c.post(url, json=body, headers=headers)
        if r.status_code != 200:
            print(f"[voice] elevenlabs {r.status_code}: {r.text[:200]}", flush=True)
            return None
        return base64.b64encode(r.content).decode("ascii")
    except Exception as e:
        import traceback
        print(f"[voice] elevenlabs exception: {e!r}", flush=True)
        traceback.print_exc()
        return None


@router.post("/voice", response_model=VoiceResponse)
async def voice(req: VoiceRequest) -> VoiceResponse:
    if not req.transcript.strip():
        raise HTTPException(400, "transcript is empty")
    reply_text, emotion, llm_source, nav = await _claude_reply(req)
    audio_b64 = await _elevenlabs_tts(reply_text)
    return VoiceResponse(
        reply_text=reply_text,
        audio_b64=audio_b64,
        emotion=emotion,
        voice_source="elevenlabs" if audio_b64 else "none",
        llm_source=llm_source,
        nav=nav,
    )


@router.get("/voice/status")
def voice_status() -> dict[str, Any]:
    return {
        "anthropic": bool(_ANTHROPIC_KEY and _ANTHROPIC_KEY.startswith("sk-ant-")),
        "elevenlabs": bool(_ELEVENLABS_KEY),
        "model": _CLAUDE_MODEL,
        "voice_id": _ELEVENLABS_VOICE,
    }
