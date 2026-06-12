"""Phase 13 shadow-mode demo: 3-line integration.

This is the *client* side. It shows how a production app wraps its primary LLM
call with ``evalgate.shadow.shadow(...)`` so a fraction of traffic also runs a
candidate prompt and fire-and-forgets a scored pair to a running EvalGate API.

Run (needs ``evalgate-api`` up, and a local Ollama unless ``EVALGATE_MOCK_LLM=1``)::

    EVALGATE_MOCK_LLM=1 EVALGATE_API_URL=http://127.0.0.1:8000 \
        PYTHONPATH='src:.' python -m examples.shadow_demo.app

For a fully offline end-to-end (1k traffic -> rolling report -> alert) without a
server, see ``scripts/phase13_shadow_smoke.py``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from evalgate.judge.prompt_spec import load_prompt_spec
from evalgate.shadow import drain_background_tasks, shadow

HERE = Path(__file__).resolve().parent

QUESTIONS = [
    "How do I get a refund for a duplicate charge?",
    "What is your data retention policy?",
    "Can I change my billing cycle to annual?",
    "My invoice shows tax I shouldn't be charged — what do I do?",
    "How do I export my account data?",
]


async def main() -> None:
    primary = load_prompt_spec(HERE / "primary.yaml")
    candidate = load_prompt_spec(HERE / "candidate.yaml")

    for q in QUESTIONS:
        # --- the integration: primary served to the user, candidate shadowed ---
        answer = await shadow(
            {"question": q},
            primary=primary,
            candidate=candidate,
            sample_rate=1.0,  # demo: shadow every request; prod would use ~0.1
            tags=["billing"],
        )
        print(f"Q: {q}\nA: {answer}\n")

    # Background pushes are fire-and-forget; in a long-lived server you'd never
    # await them. For a short script we drain so the demo doesn't exit early.
    await drain_background_tasks()
    print("shadow observations pushed (fire-and-forget).")


if __name__ == "__main__":
    asyncio.run(main())
