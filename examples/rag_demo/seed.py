"""Seed a 5-case RAG eval set into a configured Postgres / SQLite DB.

Usage::

    DATABASE_URL=postgresql+asyncpg://... python -m examples.rag_demo.seed

Idempotency: re-running creates a *new* eval set each time (with the same
``name``); ``resolve_set_id`` always picks the latest, so the smoke
script and CLI behave deterministically. Cases attach to whichever set
this run creates — we never mutate previous sets.
"""

from __future__ import annotations

import asyncio
import os

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from evalgate.core.schemas import TaskKind
from evalgate.eval_set import repository

# Each case keeps a small slice of the corpus as the gold reference
# contexts (used by ragas as ``reference_contexts``). The dynamic
# retriever is free to surface different chunks at run time — that's
# precisely what ``context_precision`` is grading.
CASES: list[dict[str, object]] = [
    {
        "question": "When are Acme invoices due?",
        "answer": (
            "Invoices are issued on the first business day of each month and are due 14 days "
            "later. Late payments incur a 1.5% monthly finance charge."
        ),
        "contexts": [
            (
                "Acme bills monthly. Invoices are issued on the first business day of each month "
                "and are due 14 days later. Late payments incur a 1.5% monthly finance charge."
            ),
        ],
        "tags": ["billing", "invoices"],
    },
    {
        "question": "Do I get a refund if I downgrade my plan mid-cycle?",
        "answer": (
            "Downgrades take effect at the start of the next billing cycle and do not generate "
            "prorated credits, so there is no mid-cycle refund."
        ),
        "contexts": [
            (
                "Mid-cycle plan upgrades are prorated on a per-day basis. The proration credit "
                "appears on the invoice that follows the upgrade. Downgrades take effect at the "
                "start of the next billing cycle and do not generate prorated credits."
            ),
        ],
        "tags": ["billing", "prorations"],
    },
    {
        "question": "What happens if my card payment fails three times?",
        "answer": (
            "After three failed payment attempts the account is suspended; access is restored "
            "within one hour of a successful payment."
        ),
        "contexts": [
            (
                "If a payment fails, Acme retries the charge after 24 hours, then again after 72 "
                "hours. After three failed attempts the account is suspended; access is restored "
                "within one hour of a successful payment."
            ),
        ],
        "tags": ["billing", "payments"],
    },
    {
        "question": "Is SMS-based multi-factor authentication still supported?",
        "answer": (
            "No. SMS-based MFA was deprecated in 2024. Supported factors are TOTP authenticator "
            "apps and hardware security keys."
        ),
        "contexts": [
            (
                "Multi-factor authentication is mandatory for accounts on the growth and "
                "enterprise plans. Supported factors are TOTP authenticator apps and hardware "
                "security keys; SMS-based MFA was deprecated in 2024."
            ),
        ],
        "tags": ["auth", "mfa"],
    },
    {
        "question": "Can I recover my account after I delete it?",
        "answer": (
            "Yes, within the 30-day grace period the account can be restored by contacting "
            "support. After 30 days only billing records remain for tax compliance."
        ),
        "contexts": [
            (
                "Account deletion is irreversible and removes all data within 30 days. During "
                "the 30-day window the account can be restored by contacting support. After 30 "
                "days, only billing records are retained for tax compliance reasons."
            ),
        ],
        "tags": ["account", "deletion"],
    },
]

SET_NAME = "rag-demo"


async def seed(database_url: str) -> str:
    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        s = await repository.create_eval_set(
            session,
            name=SET_NAME,
            description="Phase 8 RAG demo (5 billing/account questions).",
        )
        for case in CASES:
            await repository.add_case(
                session,
                set_id=s.id,
                task_type=TaskKind.rag,
                input={"question": case["question"]},
                expected={"answer": case["answer"]},
                tags=list(case.get("tags") or []),
                retrieved_contexts=list(case["contexts"]),
            )
    await engine.dispose()
    return s.id


def main() -> None:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL is required")
    set_id = asyncio.run(seed(url))
    print(f"created eval_set id={set_id} name={SET_NAME} cases={len(CASES)}")


if __name__ == "__main__":
    main()
