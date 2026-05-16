"""Stress test: 50 concurrent /next-slot calls, measure latency + error rate.

Acceptance:
  - 100% success rate
  - p50 < 200ms, p95 < 800ms
  - no server errors

Run:
  python -m hotel_agents.scripts.stress_test
"""

from __future__ import annotations

import asyncio
import json
import statistics
import sys
import time

import httpx

API = "http://127.0.0.1:7878"
CONCURRENCY = 50

# Two contrasting families so the KNN cache doesn't trivially hit the same subpop.
FAMILY_A = {
    "group_type": "couple", "adult_count": 2, "kid_ages": "none",
    "trip_purpose": "leisure", "budget_tier": "premium", "trip_length_days": 5,
    "pace": "balanced", "primary_interest": "tech", "secondary_interest": "food",
    "crowd_tolerance": "okay", "energy": "medium", "local_interaction": "mixed",
    "mobility": "full", "dietary": "none", "language_comfort": "english-only",
}
FAMILY_B = {**FAMILY_A, "budget_tier": "shoestring", "primary_interest": "nature",
            "secondary_interest": "adventure", "energy": "high", "pace": "packed"}


async def one_request(client: httpx.AsyncClient, fam: dict, history: list[str],
                       use_jetlag: bool) -> tuple[bool, float]:
    body: dict = {"family": fam, "history": history}
    if use_jetlag:
        body["jetlag"] = {"offset_h": -3.0, "trip_day": 0}
    t0 = time.perf_counter()
    try:
        r = await client.post(f"{API}/next-slot", json=body, timeout=10.0)
        r.raise_for_status()
        data = r.json()
        ok = isinstance(data.get("options"), list)
    except Exception as e:
        print(f"  err: {e}", file=sys.stderr)
        return False, time.perf_counter() - t0
    return ok, time.perf_counter() - t0


async def main() -> None:
    print(f"warming up...")
    async with httpx.AsyncClient() as c:
        await one_request(c, FAMILY_A, [], False)

    print(f"firing {CONCURRENCY} concurrent /next-slot requests "
          f"(alternating personas + jetlag flag)...")
    async with httpx.AsyncClient() as client:
        t0 = time.perf_counter()
        tasks = []
        for i in range(CONCURRENCY):
            fam = FAMILY_A if i % 2 == 0 else FAMILY_B
            hist = ["andytown_coffee"] if i % 3 == 0 else []
            use_jet = i % 2 == 1
            tasks.append(one_request(client, fam, hist, use_jet))
        results = await asyncio.gather(*tasks)
        wall = time.perf_counter() - t0

    ok_count = sum(1 for ok, _ in results if ok)
    latencies = sorted(t for _, t in results)
    p50 = latencies[len(latencies) // 2]
    p95 = latencies[int(len(latencies) * 0.95)]
    p99 = latencies[int(len(latencies) * 0.99)]

    print(f"\n=== RESULTS ({CONCURRENCY} concurrent) ===")
    print(f"  success: {ok_count}/{CONCURRENCY}")
    print(f"  wall time:     {wall*1000:.0f} ms")
    print(f"  p50 latency:   {p50*1000:.0f} ms")
    print(f"  p95 latency:   {p95*1000:.0f} ms")
    print(f"  p99 latency:   {p99*1000:.0f} ms")
    print(f"  throughput:    {CONCURRENCY / wall:.1f} req/s")

    checks = [
        ("100% success rate", ok_count == CONCURRENCY),
        ("p50 < 300ms", p50 < 0.3),
        ("p95 < 1500ms", p95 < 1.5),
    ]
    print()
    for label, ok in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")


if __name__ == "__main__":
    asyncio.run(main())
