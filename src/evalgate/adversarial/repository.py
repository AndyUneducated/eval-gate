"""Adversarial case persistence + review lifecycle (Phase 14).

Owns the DB side of the red-team flywheel:

- :func:`generate_into_set` — pick exemplars for the weak ``tag``, call
  :func:`evalgate.adversarial.synth.synthesize`, and insert each generated case
  as ``status=pending source=adversarial`` (plus its set membership). Pending
  cases are invisible to the runner (``list_cases`` defaults to active-only).
- :func:`review_case` — human-in-the-loop: ``approve`` → ``active``,
  ``reject`` → ``archived``.
- :func:`stats` — hit-rate report over the approved adversarial cases: a
  *hit* is an adversarial case whose latest candidate ``score`` falls below an
  absolute ``threshold`` (default ``0.5``) — i.e. the red team caught a real
  weakness.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from evalgate.adversarial.synth import GeneratedCase, synthesize
from evalgate.core.errors import EvalGateError
from evalgate.core.schemas import CaseSource, CaseStatus
from evalgate.db.models import EvalCaseRow, EvalResultRow
from evalgate.db.query_helpers import latest_by
from evalgate.eval_set import repository as set_repo

DEFAULT_HIT_THRESHOLD = 0.5

_REVIEW_DECISIONS: dict[str, CaseStatus] = {
    "approve": CaseStatus.active,
    "reject": CaseStatus.archived,
}


class CaseNotFoundError(EvalGateError, LookupError):
    """Raised when review targets an eval_case id that doesn't exist."""

    http_status = 404
    exit_code = 1
    slug = "case_not_found"


class BadDecisionError(EvalGateError, ValueError):
    """Raised when a review decision is neither ``approve`` nor ``reject``."""

    http_status = 422
    exit_code = 2
    slug = "bad_decision"


@dataclass
class AdversarialStats:
    """Hit-rate over approved adversarial cases in a set."""

    total: int
    evaluated: int
    hits: int
    hit_rate: float
    threshold: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "evaluated": self.evaluated,
            "hits": self.hits,
            "hit_rate": self.hit_rate,
            "threshold": self.threshold,
        }


async def _latest_score_by_case(session: AsyncSession, case_ids: set[str]) -> dict[str, float]:
    """Map case_id -> most-recent ``eval_results.score`` for the given cases.

    One query over all results for the candidate cases; we keep the latest by
    ``created_at`` in Python (dialect-agnostic, avoids a window function).
    """
    if not case_ids:
        return {}
    stmt = (
        select(EvalResultRow)
        .where(EvalResultRow.eval_case_id.in_(case_ids))
        .order_by(EvalResultRow.created_at)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return latest_by(
        (r for r in rows if r.eval_case_id is not None),
        key=lambda r: r.eval_case_id,
        value=lambda r: float(r.score),
    )


async def _gather_exemplars(
    session: AsyncSession, *, set_id: str, tag: str, limit: int = 5
) -> list[dict[str, Any]]:
    """Real cases of ``tag`` in the set, weakest (lowest recent score) first.

    Falls back to newest-first when a case has no eval results yet. Returns
    plain ``{"input": {...}}`` dicts — all :func:`synthesize` needs to mirror
    the domain's style + input key.
    """
    cases = await set_repo.list_cases(session, set_id, statuses=None)
    tagged = [c for c in cases if tag in (c.tags or [])]
    if not tagged:
        return []

    scores = await _latest_score_by_case(session, {c.id for c in tagged})

    # Lowest recent score first (the weakest cases are the best exemplars for
    # "what trips this tag up"); unscored cases sort last by newest created_at.
    def _rank(case: EvalCaseRow) -> tuple[int, float, float]:
        if case.id in scores:
            return (0, scores[case.id], 0.0)
        return (1, 0.0, -case.created_at.timestamp())

    tagged.sort(key=_rank)
    return [{"input": dict(c.input or {})} for c in tagged[:limit]]


async def generate_into_set(
    session: AsyncSession,
    *,
    set_id_or_name: str,
    tag: str,
    k: int = 10,
    model: str = "ollama/qwen3.5:9b",
    mock: bool = False,
) -> list[EvalCaseRow]:
    """Synthesize ``k`` adversarial cases for ``tag`` and insert them as pending.

    Each inserted case is ``status=pending source=adversarial`` so it stays out
    of every gate run until a human approves it via :func:`review_case`.
    Returns the freshly-inserted rows (empty if generation produced nothing).
    """
    set_id = await set_repo.resolve_set_id(session, set_id_or_name)
    exemplars = await _gather_exemplars(session, set_id=set_id, tag=tag)
    generated: list[GeneratedCase] = await synthesize(
        tag=tag,
        exemplars=exemplars,
        k=k,
        model=model,
        mock=mock,
    )

    inserted: list[EvalCaseRow] = []
    for gc in generated:
        row = await set_repo.add_case(
            session,
            set_id=set_id,
            input=gc.input,
            tags=list(gc.tags),
            status=CaseStatus.pending,
            source=CaseSource.adversarial,
        )
        inserted.append(row)
    return inserted


async def list_pending(session: AsyncSession, *, set_id_or_name: str) -> list[EvalCaseRow]:
    """Pending adversarial cases in the set, awaiting human review."""
    set_id = await set_repo.resolve_set_id(session, set_id_or_name)
    cases = await set_repo.list_cases(session, set_id, statuses=(CaseStatus.pending.value,))
    return [c for c in cases if c.source == CaseSource.adversarial.value]


async def review_case(session: AsyncSession, *, case_id: str, decision: str) -> EvalCaseRow:
    """Flip a pending case to ``active`` (approve) or ``archived`` (reject).

    Raises :class:`CaseNotFoundError` (→ 404) for an unknown id and
    ``ValueError`` (→ 422) for a decision other than ``approve`` / ``reject``.
    """
    target = _REVIEW_DECISIONS.get(decision)
    if target is None:
        raise BadDecisionError(
            f"unknown review decision {decision!r}; expected 'approve' or 'reject'"
        )
    row = await session.get(EvalCaseRow, case_id)
    if row is None:
        raise CaseNotFoundError(f"no eval_case with id {case_id!r}")
    row.status = target.value
    await session.commit()
    await session.refresh(row)
    return row


async def stats(
    session: AsyncSession,
    *,
    set_id_or_name: str,
    threshold: float = DEFAULT_HIT_THRESHOLD,
) -> AdversarialStats:
    """Hit-rate over *approved* adversarial cases in the set.

    ``hit = latest candidate score < threshold``. ``evaluated`` counts the
    approved adversarial cases that have at least one result; ``hit_rate`` is
    ``hits / evaluated`` (``0.0`` when nothing's been evaluated yet).
    """
    set_id = await set_repo.resolve_set_id(session, set_id_or_name)
    cases = await set_repo.list_cases(session, set_id, statuses=(CaseStatus.active.value,))
    adversarial = [c for c in cases if c.source == CaseSource.adversarial.value]
    total = len(adversarial)

    scores = await _latest_score_by_case(session, {c.id for c in adversarial})
    evaluated = 0
    hits = 0
    for case in adversarial:
        if case.id not in scores:
            continue
        evaluated += 1
        if scores[case.id] < threshold:
            hits += 1

    hit_rate = (hits / evaluated) if evaluated else 0.0
    return AdversarialStats(
        total=total,
        evaluated=evaluated,
        hits=hits,
        hit_rate=hit_rate,
        threshold=threshold,
    )
