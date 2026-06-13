"""Human-label persistence + calibration fit/report orchestration (Phase 16).

DB + IO side of judge calibration:

- :func:`add_label` / :func:`list_labels` — the ``human_labels`` ground-truth
  store (a binary ``good``/``bad`` verdict on an ``eval_results`` row).
- :func:`fetch_scored_labels` — join labels to results, pairing each judge
  ``score`` with its latest human label (``good`` -> 1, ``bad`` -> 0).
- :func:`fit_and_save` — fit a temperature, write ``calibration_params.json``,
  return a :class:`CalibrationReport`.
- :func:`load_calibrator` / :func:`compute_report` — read-time consumers (badcase
  ranking, ``evalgate calibration report``).

The pure math is in [report/calibration.py](../report/calibration.py); raw judge
scores are never mutated — calibration is applied read-time from the params file.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from evalgate.core.errors import EvalGateError
from evalgate.core.schemas import (
    CalibrationReport,
    HumanLabel,
    ReliabilityBin,
)
from evalgate.db.models import EvalResultRow, HumanLabelRow
from evalgate.db.query_helpers import latest_by
from evalgate.report import calibration as cal


class ResultNotFoundError(EvalGateError, LookupError):
    """Raised when a label targets an eval_result id that doesn't exist."""

    http_status = 404
    exit_code = 1
    slug = "result_not_found"


class InsufficientLabelsError(EvalGateError, RuntimeError):
    """Raised when there aren't enough labeled, scored rows to calibrate."""

    http_status = 422
    exit_code = 2
    slug = "insufficient_labels"


def _new_id() -> str:
    return uuid4().hex


async def add_label(
    session: AsyncSession,
    *,
    eval_result_id: str,
    label: str | HumanLabel,
    annotator: str = "human",
    note: str | None = None,
) -> HumanLabelRow:
    """Attach a human good/bad verdict to a judged result."""
    label = HumanLabel(str(label))
    result = await session.get(EvalResultRow, eval_result_id)
    if result is None:
        raise ResultNotFoundError(f"no eval_result with id {eval_result_id!r}")
    row = HumanLabelRow(
        id=_new_id(),
        eval_result_id=eval_result_id,
        label=label.value,
        annotator=annotator,
        note=note,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def list_labels(
    session: AsyncSession, *, eval_result_id: str | None = None
) -> list[HumanLabelRow]:
    stmt = select(HumanLabelRow).order_by(HumanLabelRow.created_at)
    if eval_result_id is not None:
        stmt = stmt.where(HumanLabelRow.eval_result_id == eval_result_id)
    return list((await session.execute(stmt)).scalars().all())


async def fetch_scored_labels(
    session: AsyncSession, *, run_id: str | None = None
) -> tuple[list[float], list[int], list[str]]:
    """Pair each result's judge ``score`` with its latest human label.

    Returns ``(scores, labels, result_ids)`` where ``label`` is ``1`` for
    ``good`` / ``0`` for ``bad``. Results without a score are skipped; when a
    result has multiple labels, the most recent one wins.
    """
    label_stmt = select(HumanLabelRow).order_by(HumanLabelRow.created_at)
    label_rows = (await session.execute(label_stmt)).scalars().all()
    latest = latest_by(
        label_rows,
        key=lambda r: r.eval_result_id,
        value=lambda r: r.label,
    )

    if not latest:
        return [], [], []

    result_stmt = select(EvalResultRow).where(EvalResultRow.id.in_(latest.keys()))
    if run_id is not None:
        result_stmt = result_stmt.where(EvalResultRow.eval_run_id == run_id)
    result_rows = (await session.execute(result_stmt)).scalars().all()

    scores: list[float] = []
    labels: list[int] = []
    result_ids: list[str] = []
    for r in result_rows:
        if r.score is None:
            continue
        scores.append(float(r.score))
        labels.append(1 if latest[r.id] == HumanLabel.good.value else 0)
        result_ids.append(r.id)
    return scores, labels, result_ids


def _bins_to_schema(points: list[cal.ReliabilityPoint]) -> list[ReliabilityBin]:
    return [
        ReliabilityBin(
            bin_lower=p.bin_lower,
            bin_upper=p.bin_upper,
            count=p.count,
            mean_confidence=p.mean_confidence,
            mean_accuracy=p.mean_accuracy,
        )
        for p in points
    ]


def _stats_to_report(stats: cal.CalibrationStats) -> CalibrationReport:
    return CalibrationReport(
        n=stats.n,
        n_bins=stats.n_bins,
        temperature=stats.temperature,
        ece_before=stats.ece_before,
        ece_after=stats.ece_after,
        mce_before=stats.mce_before,
        mce_after=stats.mce_after,
        reliability_before=_bins_to_schema(stats.reliability_before),
        reliability_after=_bins_to_schema(stats.reliability_after),
    )


async def compute_report(
    session: AsyncSession,
    *,
    calibrator: cal.Calibrator | None = None,
    run_id: str | None = None,
    n_bins: int = cal.DEFAULT_N_BINS,
) -> tuple[CalibrationReport, cal.CalibrationStats]:
    """Build a before/after report over the current labeled set.

    Uses ``calibrator`` when provided (e.g. a previously fitted/saved one);
    otherwise fits an ephemeral temperature from the same data.
    """
    scores, labels, _ = await fetch_scored_labels(session, run_id=run_id)
    if not scores:
        raise InsufficientLabelsError("no labeled, scored results to report on")
    if calibrator is None:
        calibrator = cal.Calibrator(cal.fit_temperature(scores, labels))
    stats = cal.evaluate_calibration(scores, labels, calibrator, n_bins=n_bins)
    return _stats_to_report(stats), stats


async def fit_and_save(
    session: AsyncSession,
    *,
    params_path: str,
    run_id: str | None = None,
    n_bins: int = cal.DEFAULT_N_BINS,
) -> CalibrationReport:
    """Fit a temperature on the labeled set and persist it to ``params_path``."""
    scores, labels, _ = await fetch_scored_labels(session, run_id=run_id)
    if len(scores) < cal._MIN_LABELS or len(set(labels)) < 2:
        raise InsufficientLabelsError(
            f"need >= {cal._MIN_LABELS} labeled, scored rows spanning both "
            f"good and bad to fit a temperature (got {len(scores)})"
        )
    temperature = cal.fit_temperature(scores, labels)
    calibrator = cal.Calibrator(temperature)
    stats = cal.evaluate_calibration(scores, labels, calibrator, n_bins=n_bins)

    payload = {
        "temperature": temperature,
        "scope": "global",
        "n": stats.n,
        "ece_before": stats.ece_before,
        "ece_after": stats.ece_after,
        "mce_before": stats.mce_before,
        "mce_after": stats.mce_after,
        "fitted_at": datetime.now(UTC).isoformat(),
    }
    path = Path(params_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2))
    return _stats_to_report(stats)


def load_calibrator(params_path: str) -> cal.Calibrator | None:
    """Load a fitted Calibrator from disk, or ``None`` if the file is absent."""
    path = Path(params_path)
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    return cal.Calibrator.from_dict(data)
