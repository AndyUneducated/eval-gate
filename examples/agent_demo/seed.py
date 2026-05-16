"""Seed a 3-case agent trajectory demo set."""

from __future__ import annotations

import asyncio
import os

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from evalgate.core.schemas import TaskKind
from evalgate.eval_set import repository

SET_NAME = "agent-demo"

CASES: list[dict[str, object]] = [
    {
        "question": "User asks why invoice INV-42 is still unpaid.",
        "answer": "Invoice INV-42 is open and due in 14 days.",
        "tags": ["billing", "agent"],
        "expected_trajectory": [
            {"tool": "lookup_invoice", "args": {}},
            {"tool": "fetch_policy", "args": {}},
        ],
    },
    {
        "question": "User asks if retry policy applies after failed payments.",
        "answer": "The billing retry policy applies after failed attempts.",
        "tags": ["payments", "agent"],
        "expected_trajectory": [
            {"tool": "get_payment_attempts", "args": {}},
            {"tool": "fetch_policy", "args": {}},
        ],
    },
    {
        "question": "User asks both invoice status and policy context.",
        "answer": "Invoice is open; late-payment policy applies.",
        "tags": ["billing", "policy", "agent"],
        "expected_trajectory": [
            {"tool": "lookup_invoice", "args": {}},
            {"tool": "fetch_policy", "args": {}},
        ],
    },
]


async def seed(database_url: str) -> str:
    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        s = await repository.create_eval_set(
            session,
            name=SET_NAME,
            description="Phase 9 agent trajectory demo (3 multi-step cases).",
        )
        for case in CASES:
            await repository.add_case(
                session,
                set_id=s.id,
                task_type=TaskKind.agent,
                input={"question": case["question"]},
                expected={"answer": case["answer"]},
                tags=list(case["tags"]),
                expected_trajectory=list(case["expected_trajectory"]),
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
