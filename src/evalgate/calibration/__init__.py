"""Phase 16 Judge Calibration: human-label store + temperature-scaling fit.

The pure statistics live in [report/calibration.py](../report/calibration.py);
this package owns the DB side (the ``human_labels`` ground-truth table) and the
fit/report orchestration (reading ``(score, label)`` pairs, writing the fitted
params JSON, loading a read-time :class:`~evalgate.report.calibration.Calibrator`).
"""

from __future__ import annotations

from evalgate.calibration.repository import (
    InsufficientLabelsError,
    ResultNotFoundError,
    add_label,
    compute_report,
    fetch_scored_labels,
    fit_and_save,
    list_labels,
    load_calibrator,
)

__all__ = [
    "InsufficientLabelsError",
    "ResultNotFoundError",
    "add_label",
    "compute_report",
    "fetch_scored_labels",
    "fit_and_save",
    "list_labels",
    "load_calibrator",
]
