"""BadCase Finder: pick eval_results worth promoting into an eval_set.

Three strategies, all on existing columns of `eval_results` (no new table):

1. ``uncertainty`` — sort by ``judge_confidence ASC NULLS LAST``. Low confidence
   is the canonical active-learning signal: the judge can't tell good from bad
   here, so human attention pays off the most.

2. ``outlier`` — keep rows that are *categorically* interesting:
       score == 0  OR  any safety axis_breakdown rate > 0  OR  latency > p95  OR  cost > p95
   p95 is taken within the same ``run_id`` if provided, else globally. Below
   ``MIN_FOR_PERCENTILE`` rows we skip the percentile guard (no statistical
   meaning) and fall back to ``score == 0 / safety`` only.

3. ``llm`` — take ``2 * limit`` uncertainty candidates, ask a cheap model
   "is this output SUBTLY WRONG?", keep the ones it flags. Cheapest active
   learning rung: we never trust the cheap classifier alone, but we trust it
   to *filter* uncertainty hits down to the ones worth a human review.

All three return ``list[BadCase]`` truncated to ``limit``. Order matters —
the CLI prints them in `evalgate badcase list` and users typically promote
from the top.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from evalgate.db.models import EvalResultRow
from evalgate.judge.protocol import acompletion_json
from evalgate.report.calibration import Calibrator

MIN_FOR_PERCENTILE = 4  # below this n, p95 has no statistical meaning

# Single source of truth for the badcase strategy names (CLI + REST validate
# against this, and :func:`find` dispatches on it).
VALID_STRATEGIES: tuple[str, ...] = ("uncertainty", "outlier", "llm")


@dataclass
class BadCase:
    eval_result_id: str
    eval_case_id: str | None
    eval_run_id: str
    score: float
    judge_confidence: float | None
    latency_ms: int
    cost_usd: float
    tags: list[str]
    strategy: str
    reason: str
    llm_label: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


async def _load_rows(session: AsyncSession, *, run_id: str | None) -> list[EvalResultRow]:
    stmt = select(EvalResultRow)
    if run_id is not None:
        stmt = stmt.where(EvalResultRow.eval_run_id == run_id)
    return list((await session.execute(stmt)).scalars().all())


def _output_text(row: EvalResultRow) -> str:
    out = row.output or {}
    if isinstance(out, dict):
        text = out.get("text")
        if isinstance(text, str):
            return text
    return ""


def _safety_metric_flags(row: EvalResultRow) -> list[str]:
    """Non-zero rates under ``axis_breakdown["safety"]``."""
    breakdown = row.axis_breakdown or {}
    if not isinstance(breakdown, dict):
        return []
    safety = breakdown.get("safety")
    if not isinstance(safety, dict):
        return []
    flags: list[str] = []
    for key, val in safety.items():
        try:
            if float(val) > 0:
                flags.append(str(key))
        except (TypeError, ValueError):
            continue
    return flags


def _percentile_or_none(values: list[float], *, q: float) -> float | None:
    if len(values) < MIN_FOR_PERCENTILE:
        return None
    return float(np.percentile(values, q))


def _to_badcase(
    row: EvalResultRow, *, strategy: str, reason: str, llm_label: dict | None = None
) -> BadCase:
    return BadCase(
        eval_result_id=row.id,
        eval_case_id=row.eval_case_id,
        eval_run_id=row.eval_run_id,
        score=float(row.score),
        judge_confidence=row.judge_confidence,
        latency_ms=int(row.latency_ms),
        cost_usd=float(row.cost_usd),
        tags=list(row.tags or []),
        strategy=strategy,
        reason=reason,
        llm_label=llm_label,
    )


async def find_uncertainty(
    session: AsyncSession,
    *,
    run_id: str | None = None,
    limit: int = 20,
    calibrator: Calibrator | None = None,
) -> list[BadCase]:
    rows = await _load_rows(session, run_id=run_id)
    if calibrator is not None:
        # Phase 16: rank by *calibrated* uncertainty — closeness of the
        # calibrated P(good) to 0.5 (the decision boundary). This is the
        # principled active-learning signal once the score is a real
        # probability, and it works at read time off the immutable raw score.
        # Phase 17: when the calibrator is conditional (per task_type / judge),
        # resolve each row's group so it's ranked with the right curve.
        group_of: dict[str, str] = {}
        if calibrator.scope != "global":
            from evalgate.calibration.repository import group_keys_for_rows

            group_of = await group_keys_for_rows(session, rows, scope=calibrator.scope)
        rows.sort(key=lambda r: -calibrator.uncertainty(float(r.score), group_of.get(r.id)))
        out: list[BadCase] = []
        for r in rows[:limit]:
            g = group_of.get(r.id)
            p = calibrator.transform(float(r.score), g)
            u = 1.0 - abs(2.0 * p - 1.0)
            reason = f"calibrated_uncertainty={u:.3f} (p_good={p:.3f})"
            if g is not None:
                reason += f" [{calibrator.scope}={g}]"
            out.append(_to_badcase(r, strategy="uncertainty", reason=reason))
        return out

    # NULL judge_confidence is the *most* unknown — sort to the end, not the
    # start: a missing-confidence row is "couldn't even get a signal" and
    # rarely useful for promotion. Users wanting them can re-run Phase 6.
    rows.sort(key=lambda r: (r.judge_confidence is None, r.judge_confidence or 0.0))
    out = []
    for r in rows[:limit]:
        if r.judge_confidence is None:
            reason = "no confidence signal"
        else:
            reason = f"judge_confidence={r.judge_confidence:.3f}"
        out.append(_to_badcase(r, strategy="uncertainty", reason=reason))
    return out


async def find_outlier(
    session: AsyncSession, *, run_id: str | None = None, limit: int = 20
) -> list[BadCase]:
    rows = await _load_rows(session, run_id=run_id)
    if not rows:
        return []

    latencies = [float(r.latency_ms) for r in rows]
    costs = [float(r.cost_usd) for r in rows]
    lat_p95 = _percentile_or_none(latencies, q=95)
    cost_p95 = _percentile_or_none(costs, q=95)

    flagged: list[tuple[tuple[int, float, int], EvalResultRow, str]] = []
    for r in rows:
        reasons: list[str] = []
        if float(r.score) == 0.0:
            reasons.append("score=0")
        safety_flags = _safety_metric_flags(r)
        if safety_flags:
            reasons.append(f"safety:{','.join(safety_flags)}")
        if lat_p95 is not None and r.latency_ms > lat_p95:
            reasons.append(f"latency_ms={r.latency_ms} > p95={lat_p95:.0f}")
        if cost_p95 is not None and r.cost_usd > cost_p95:
            reasons.append(f"cost_usd={r.cost_usd:.4f} > p95={cost_p95:.4f}")
        if reasons:
            # Severity = number of triggered conditions; ties broken by lower
            # score and higher latency so the "obviously bad and slow" cases
            # surface first.
            severity = (
                -len(reasons),
                float(r.score),
                -int(r.latency_ms),
            )
            flagged.append((severity, r, "; ".join(reasons)))

    flagged.sort(key=lambda t: t[0])
    return [_to_badcase(r, strategy="outlier", reason=reason) for _, r, reason in flagged[:limit]]


_LLM_PROMPT = """Given an LLM input and its candidate output, decide whether the output is
SUBTLY WRONG (correct-looking but inaccurate / unhelpful / off-spec).
Return STRICT JSON: {{"subtle_bad": true|false, "reason": "<one sentence>"}}.

INPUT:
{input}

OUTPUT:
{output}
"""


async def _classify_subtle_bad(
    *,
    case_input: dict[str, Any],
    candidate_output: str,
    model: str,
    mock: bool,
) -> dict[str, Any]:
    """Single-shot cheap-model classifier. Returns the parsed JSON dict.

    Robust to non-JSON output: any parse failure becomes
    ``{"subtle_bad": False, "reason": "<truncated raw>"}`` — we never raise
    out of badcase finding.
    """
    import json
    import re

    prompt = _LLM_PROMPT.format(input=json.dumps(case_input), output=candidate_output)
    mock_response = '{"subtle_bad": true, "reason": "mock-flag"}' if mock else None
    text, _ = await acompletion_json(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        mock_response=mock_response,
    )
    try:
        payload = json.loads(text)
        if isinstance(payload, dict) and "subtle_bad" in payload:
            return {
                "subtle_bad": bool(payload["subtle_bad"]),
                "reason": str(payload.get("reason") or ""),
            }
    except (json.JSONDecodeError, TypeError):
        pass

    m = re.search(r'"?subtle_bad"?\s*[:=]\s*(true|false)', text, re.IGNORECASE)
    if m:
        return {"subtle_bad": m.group(1).lower() == "true", "reason": text[:200]}
    return {"subtle_bad": False, "reason": text[:200]}


async def find_llm(
    session: AsyncSession,
    *,
    run_id: str | None = None,
    limit: int = 20,
    cheap_model: str = "ollama/qwen3.5:9b",
    mock: bool = False,
) -> list[BadCase]:
    # Two-pass funnel: get 2*limit uncertainty candidates, then ask the cheap
    # model. This keeps LLM calls bounded by `limit` regardless of run size.
    candidates = await find_uncertainty(session, run_id=run_id, limit=limit * 2)
    if not candidates:
        return []

    # Fetch only the candidate rows (bounded ``IN`` query) rather than
    # re-scanning the whole table a second time.
    from evalgate.db.models import EvalCaseRow

    cand_ids = [c.eval_result_id for c in candidates]
    by_id = {
        r.id: r
        for r in (
            await session.execute(select(EvalResultRow).where(EvalResultRow.id.in_(cand_ids)))
        ).scalars()
    }

    # Batch-load the backing cases in one query (avoids an N+1 ``session.get``
    # per candidate); pass {} when a case is missing — the classifier degrades.
    case_ids = {r.eval_case_id for r in by_id.values() if r.eval_case_id}
    cases_by_id: dict[str, EvalCaseRow] = {}
    if case_ids:
        cases_by_id = {
            c.id: c
            for c in (
                await session.execute(select(EvalCaseRow).where(EvalCaseRow.id.in_(case_ids)))
            ).scalars()
        }

    out: list[BadCase] = []
    for c in candidates:
        if len(out) >= limit:
            break
        row = by_id.get(c.eval_result_id)
        if row is None:
            continue
        case_input: dict[str, Any] = {}
        if row.eval_case_id:
            case = cases_by_id.get(row.eval_case_id)
            if case is not None:
                case_input = dict(case.input or {})
        label = await _classify_subtle_bad(
            case_input=case_input,
            candidate_output=_output_text(row),
            model=cheap_model,
            mock=mock,
        )
        if label.get("subtle_bad"):
            out.append(
                _to_badcase(
                    row,
                    strategy="llm",
                    reason=label.get("reason") or "subtle_bad=true",
                    llm_label=label,
                )
            )
    return out


async def find(
    session: AsyncSession,
    *,
    strategy: str,
    run_id: str | None = None,
    limit: int = 20,
    mock: bool = False,
    cheap_model: str = "ollama/qwen3.5:9b",
    calibrator: Calibrator | None = None,
) -> list[BadCase]:
    """Dispatch by strategy name. Raises ``ValueError`` on unknown strategy."""
    if strategy not in VALID_STRATEGIES:
        raise ValueError(
            f"unknown badcase strategy {strategy!r}; expected one of {VALID_STRATEGIES}"
        )
    if strategy == "uncertainty":
        return await find_uncertainty(session, run_id=run_id, limit=limit, calibrator=calibrator)
    if strategy == "outlier":
        return await find_outlier(session, run_id=run_id, limit=limit)
    if strategy == "llm":
        return await find_llm(
            session,
            run_id=run_id,
            limit=limit,
            cheap_model=cheap_model,
            mock=mock,
        )
    # Unreachable: the guard above already rejected unknown strategies.
    raise ValueError(f"unhandled badcase strategy {strategy!r}")
