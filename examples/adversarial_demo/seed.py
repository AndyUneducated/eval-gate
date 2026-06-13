"""Seed a Phase 14 adversarial-synth demo eval set.

A small ``billing`` eval set of ordinary, well-behaved cases (the "happy path"
the team already ships). Phase 14's red-team synthesizer then generates tricky
``billing``-tagged adversarial cases *into the same set* as ``pending`` rows;
after human approval they join the active set and the weakened candidate prompt
(``prompts/adversarial_candidate.yaml``) fails them.

Cases use the ``question`` input key so the synthesizer mirrors it and the
candidate template renders ``{question}`` uniformly across base + adversarial
cases.
"""

from __future__ import annotations

import asyncio
import os

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from evalgate.eval_set import repository

SET_NAME = "adversarial-billing-demo"
TAG = "billing"

BASE_CASES: list[dict] = [
    {
        "question": "When are Acme invoices due each month?",
        "answer": "Invoices are issued on the first business day and due 14 days later.",
    },
    {
        "question": "Do I get a prorated refund if I downgrade mid-cycle?",
        "answer": "Downgrades take effect next cycle; no prorated refund is issued.",
    },
    {
        "question": "How do I update the credit card on my account?",
        "answer": "Open Billing → Payment Methods and replace the card on file.",
    },
    {
        "question": "What currency are invoices billed in?",
        "answer": "Invoices are billed in USD unless your contract specifies otherwise.",
    },
]


async def seed(database_url: str) -> str:
    """Create the billing demo set + base cases. Returns the set id."""
    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        billing = await repository.create_eval_set(
            session,
            name=SET_NAME,
            description="Phase 14 adversarial-synth demo (billing happy-path cases).",
        )
        for case in BASE_CASES:
            await repository.add_case(
                session,
                set_id=billing.id,
                input={"question": case["question"]},
                expected={"answer": case["answer"]},
                tags=[TAG],
            )
    await engine.dispose()
    return billing.id


def main() -> None:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL is required")
    set_id = asyncio.run(seed(url))
    print(f"created set id={set_id} name={SET_NAME} base_cases={len(BASE_CASES)}")


if __name__ == "__main__":
    main()
