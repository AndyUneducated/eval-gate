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
from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from evalgate.core.errors import EvalGateError
from evalgate.core.schemas import (
    AgreementGroup,
    AgreementReport,
    CalibrationGroup,
    CalibrationReport,
    HumanLabel,
    ReliabilityBin,
)
from evalgate.db.models import EvalCaseRow, EvalResultRow, EvalRunRow, HumanLabelRow
from evalgate.db.query_helpers import latest_by
from evalgate.report import agreement as agr
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


async def group_keys_for_rows(
    session: AsyncSession,
    rows: Sequence[EvalResultRow],
    *,
    scope: str,
) -> dict[str, str]:
    """Map each result id to its conditional-calibration group key (Phase 17).

    ``judge_model`` reads the parent ``eval_runs.judge_model``; ``task_type``
    reads the (soft-referenced) ``eval_cases.task_type``. Missing joins collapse
    to ``"unknown"`` so a row is never dropped. ``global`` short-circuits to an
    empty map (the caller then uses the single global temperature).
    """
    if scope == cal.GLOBAL_SCOPE or not rows:
        return {}
    if scope == "judge_model":
        run_ids = {r.eval_run_id for r in rows}
        runs = (
            (await session.execute(select(EvalRunRow).where(EvalRunRow.id.in_(run_ids))))
            .scalars()
            .all()
        )
        judge_of = {run.id: run.judge_model for run in runs}
        return {r.id: judge_of.get(r.eval_run_id, "unknown") for r in rows}
    if scope == "task_type":
        case_ids = {r.eval_case_id for r in rows if r.eval_case_id}
        task_of: dict[str, str] = {}
        if case_ids:
            cases = (
                (await session.execute(select(EvalCaseRow).where(EvalCaseRow.id.in_(case_ids))))
                .scalars()
                .all()
            )
            task_of = {c.id: c.task_type for c in cases}
        return {
            r.id: (task_of.get(r.eval_case_id, "unknown") if r.eval_case_id else "unknown")
            for r in rows
        }
    raise ValueError(f"unknown calibration scope {scope!r}; expected one of {cal.VALID_SCOPES}")


async def fetch_group_keys(
    session: AsyncSession, result_ids: Sequence[str], *, scope: str
) -> dict[str, str]:
    """``group_keys_for_rows`` by id — loads the result rows first."""
    if scope == cal.GLOBAL_SCOPE or not result_ids:
        return {}
    rows = (
        (await session.execute(select(EvalResultRow).where(EvalResultRow.id.in_(list(result_ids)))))
        .scalars()
        .all()
    )
    return await group_keys_for_rows(session, rows, scope=scope)


def _fit_calibrator(
    scores: Sequence[float],
    labels: Sequence[int],
    result_ids: Sequence[str],
    group_keys: dict[str, str],
    *,
    scope: str,
) -> tuple[cal.Calibrator, dict[str, CalibrationGroup]]:
    """Fit the global temperature plus one per data-rich group (Phase 17).

    A group is only given its own curve when it clears the same ``_MIN_LABELS``
    + both-classes bar the global fit uses; thinner groups are omitted and fall
    back to the global temperature at read time.
    """
    default_t = cal.fit_temperature(scores, labels)
    group_temperatures: dict[str, float] = {}
    groups_meta: dict[str, CalibrationGroup] = {}
    if scope != cal.GLOBAL_SCOPE:
        buckets: dict[str, tuple[list[float], list[int]]] = defaultdict(lambda: ([], []))
        for sc, y, rid in zip(scores, labels, result_ids, strict=True):
            g = group_keys.get(rid, "unknown")
            buckets[g][0].append(sc)
            buckets[g][1].append(y)
        for g, (gs, gy) in sorted(buckets.items()):
            if len(gs) < cal._MIN_LABELS or len(set(gy)) < 2:
                continue
            gt = cal.fit_temperature(gs, gy)
            group_temperatures[g] = gt
            gstats = cal.evaluate_calibration(gs, gy, cal.Calibrator(gt))
            groups_meta[g] = CalibrationGroup(
                temperature=gt,
                n=len(gs),
                ece_before=gstats.ece_before,
                ece_after=gstats.ece_after,
            )
    calibrator = cal.Calibrator(
        temperature=default_t, scope=scope, group_temperatures=group_temperatures
    )
    return calibrator, groups_meta


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


def _stats_to_report(
    stats: cal.CalibrationStats,
    *,
    scope: str = cal.GLOBAL_SCOPE,
    groups: dict[str, CalibrationGroup] | None = None,
) -> CalibrationReport:
    return CalibrationReport(
        n=stats.n,
        n_bins=stats.n_bins,
        temperature=stats.temperature,
        ece_before=stats.ece_before,
        ece_after=stats.ece_after,
        mce_before=stats.mce_before,
        mce_after=stats.mce_after,
        scope=scope,
        groups=groups or {},
        reliability_before=_bins_to_schema(stats.reliability_before),
        reliability_after=_bins_to_schema(stats.reliability_after),
    )


async def compute_report(
    session: AsyncSession,
    *,
    calibrator: cal.Calibrator | None = None,
    run_id: str | None = None,
    n_bins: int = cal.DEFAULT_N_BINS,
    scope: str | None = None,
) -> tuple[CalibrationReport, cal.CalibrationStats]:
    """Build a before/after report over the current labeled set.

    Uses ``calibrator`` when provided (e.g. a previously fitted/saved one, whose
    ``scope`` is honored); otherwise fits an ephemeral (optionally conditional)
    calibrator from the same data using ``scope`` (default ``"global"``).
    """
    scores, labels, ids = await fetch_scored_labels(session, run_id=run_id)
    if not scores:
        raise InsufficientLabelsError("no labeled, scored results to report on")

    groups_meta: dict[str, CalibrationGroup] = {}
    if calibrator is None:
        eff_scope = scope or cal.GLOBAL_SCOPE
        group_keys = await fetch_group_keys(session, ids, scope=eff_scope)
        calibrator, groups_meta = _fit_calibrator(scores, labels, ids, group_keys, scope=eff_scope)
    else:
        eff_scope = calibrator.scope

    groups_seq = None
    if eff_scope != cal.GLOBAL_SCOPE:
        group_keys = await fetch_group_keys(session, ids, scope=eff_scope)
        groups_seq = [group_keys.get(rid, "unknown") for rid in ids]

    stats = cal.evaluate_calibration(scores, labels, calibrator, n_bins=n_bins, groups=groups_seq)
    return _stats_to_report(stats, scope=eff_scope, groups=groups_meta), stats


async def fit_and_save(
    session: AsyncSession,
    *,
    params_path: str,
    run_id: str | None = None,
    n_bins: int = cal.DEFAULT_N_BINS,
    scope: str = cal.GLOBAL_SCOPE,
) -> CalibrationReport:
    """Fit a (optionally conditional) calibrator and persist it to ``params_path``.

    ``scope`` is ``"global"`` (one temperature), ``"task_type"``, or
    ``"judge_model"``. For non-global scopes the global temperature is still
    fitted as the fallback, and each data-rich group additionally gets its own.
    """
    if scope not in cal.VALID_SCOPES:
        raise ValueError(f"unknown calibration scope {scope!r}; expected one of {cal.VALID_SCOPES}")
    scores, labels, ids = await fetch_scored_labels(session, run_id=run_id)
    if len(scores) < cal._MIN_LABELS or len(set(labels)) < 2:
        raise InsufficientLabelsError(
            f"need >= {cal._MIN_LABELS} labeled, scored rows spanning both "
            f"good and bad to fit a temperature (got {len(scores)})"
        )
    group_keys = await fetch_group_keys(session, ids, scope=scope)
    calibrator, groups_meta = _fit_calibrator(scores, labels, ids, group_keys, scope=scope)
    groups_seq = (
        [group_keys.get(rid, "unknown") for rid in ids] if scope != cal.GLOBAL_SCOPE else None
    )
    stats = cal.evaluate_calibration(scores, labels, calibrator, n_bins=n_bins, groups=groups_seq)

    payload = {
        "temperature": calibrator.temperature,
        "scope": scope,
        "groups": {
            name: {
                "temperature": g.temperature,
                "n": g.n,
                "ece_before": g.ece_before,
                "ece_after": g.ece_after,
            }
            for name, g in groups_meta.items()
        },
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
    return _stats_to_report(stats, scope=scope, groups=groups_meta)


def load_calibrator(params_path: str) -> cal.Calibrator | None:
    """Load a fitted Calibrator from disk, or ``None`` if the file is absent."""
    path = Path(params_path)
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    return cal.Calibrator.from_dict(data)


def _agreement_report(stats: agr.AgreementStats, *, scope: str, groups) -> AgreementReport:
    return AgreementReport(
        n=stats.n,
        threshold=stats.threshold,
        cohen_kappa=stats.cohen_kappa,
        observed_agreement=stats.observed_agreement,
        expected_agreement=stats.expected_agreement,
        ci_low=stats.ci_low,
        ci_high=stats.ci_high,
        judge_positive_rate=stats.judge_positive_rate,
        human_positive_rate=stats.human_positive_rate,
        tp=stats.confusion.tp,
        fp=stats.confusion.fp,
        fn=stats.confusion.fn,
        tn=stats.confusion.tn,
        scope=scope,
        groups=groups or {},
    )


async def compute_agreement(
    session: AsyncSession,
    *,
    run_id: str | None = None,
    threshold: float = agr.DEFAULT_THRESHOLD,
    scope: str = cal.GLOBAL_SCOPE,
) -> AgreementReport:
    """Cohen's kappa between the judge's thresholded verdict and human labels.

    Reuses the Phase 16 ``human_labels`` store (one table feeds both phases).
    For non-global ``scope`` each data-bearing task_type / judge_model slice also
    gets its own kappa.
    """
    if scope not in cal.VALID_SCOPES:
        raise ValueError(f"unknown agreement scope {scope!r}; expected one of {cal.VALID_SCOPES}")
    scores, labels, ids = await fetch_scored_labels(session, run_id=run_id)
    if not scores:
        raise InsufficientLabelsError("no labeled, scored results to measure agreement on")

    stats = agr.evaluate_agreement(scores, labels, threshold=threshold)

    groups_meta: dict[str, AgreementGroup] = {}
    if scope != cal.GLOBAL_SCOPE:
        group_keys = await fetch_group_keys(session, ids, scope=scope)
        buckets: dict[str, tuple[list[float], list[int]]] = defaultdict(lambda: ([], []))
        for sc, y, rid in zip(scores, labels, ids, strict=True):
            g = group_keys.get(rid, "unknown")
            buckets[g][0].append(sc)
            buckets[g][1].append(y)
        for g, (gs, gy) in sorted(buckets.items()):
            gstats = agr.evaluate_agreement(gs, gy, threshold=threshold)
            groups_meta[g] = AgreementGroup(
                n=gstats.n,
                cohen_kappa=gstats.cohen_kappa,
                observed_agreement=gstats.observed_agreement,
                ci_low=gstats.ci_low,
                ci_high=gstats.ci_high,
            )

    return _agreement_report(stats, scope=scope, groups=groups_meta)
