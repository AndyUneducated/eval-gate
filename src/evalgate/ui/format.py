"""Pure formatting helpers for the streamlit UI.

Kept dependency-free (no streamlit / no httpx imports) so they can be
unit-tested without the streamlit runtime. Tests live in
``tests/test_ui_format.py``.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, Literal

DeltaSemantic = Literal["regression", "improvement", "neutral"]

# Semantic delta colors (Datadog / Grafana-style). Used by Reports tables and verdict.
DELTA_COLOR_REGRESSION = "#F46060"
DELTA_COLOR_IMPROVEMENT = "#2ECC71"
DELTA_COLOR_NEUTRAL = "#9CA3AF"

_DELTA_KIND_COLORS: dict[DeltaSemantic, str] = {
    "regression": DELTA_COLOR_REGRESSION,
    "improvement": DELTA_COLOR_IMPROVEMENT,
    "neutral": DELTA_COLOR_NEUTRAL,
}

# Per-axis direction mirrors `evalgate.report.multi_axis.AXES`. We don't
# import that module to keep this helper streamlit-friendly (zero
# evalgate.report deps) and to avoid circular imports through pydantic
# model loading during streamlit's rerun cycles.
AXIS_DIRECTION: dict[str, str] = {
    "quality": "higher_is_better",
    "cost": "lower_is_better",
    "latency_p95": "lower_is_better",
    # Safety metrics are *rates of bad events* (PII leaks, jailbreak
    # compliance): a higher rate is a regression. Omitting this defaulted to
    # "higher_is_better", painting safety regressions green.
    "safety": "lower_is_better",
}


def delta_semantic_kind(delta: float, axis_name: str) -> DeltaSemantic:
    """Classify a signed delta as regression, improvement, or no change.

    Mirrors gate axis direction in :data:`AXIS_DIRECTION`: quality drops are
    regressions; cost / latency / safety increases are regressions.
    """
    if abs(delta) < 1e-9:
        return "neutral"
    direction = AXIS_DIRECTION.get(axis_name, "higher_is_better")
    regression = (direction == "lower_is_better") == (delta > 0)
    return "regression" if regression else "improvement"


def delta_css(delta: float, axis_name: str) -> str:
    """CSS ``color`` (and weight) for a delta cell in styled dataframes."""
    kind = delta_semantic_kind(delta, axis_name)
    color = _DELTA_KIND_COLORS[kind]
    if kind == "neutral":
        return f"color: {color}"
    return f"color: {color}; font-weight: 600"


def humanize_latency_ms(value: float | int | None) -> str:
    """Render a ms-resolution latency like ``245 ms`` / ``1.20 s``."""
    if value is None:
        return "—"
    ms = float(value)
    if ms < 1000:
        return f"{ms:.0f} ms"
    return f"{ms / 1000:.2f} s"


def humanize_cost_usd(value: float | int | None) -> str:
    """Render a USD cost using cents-precision ($-cheap calls render as $0.0001)."""
    if value is None:
        return "—"
    cost = float(value)
    if cost == 0:
        return "$0.00"
    if cost < 0.01:
        return f"${cost:.4f}"
    return f"${cost:.2f}"


def humanize_score(value: float | None, *, percent: bool = False) -> str:
    """Render a 0..1 score either raw (3 decimals) or as a percentage."""
    if value is None:
        return "—"
    if percent:
        return f"{float(value) * 100:.1f}%"
    return f"{float(value):.3f}"


def humanize_datetime(value: str | datetime | None) -> str:
    """Render a UTC ISO timestamp as ``YYYY-MM-DD HH:MM`` (UTC)."""
    if value is None:
        return "—"
    dt: datetime
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return str(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).strftime("%Y-%m-%d %H:%M")


def axis_status_emoji(passed: bool) -> str:
    """Tiny status indicator used in axis cards (no emoji policy: ASCII only)."""
    return "PASS" if passed else "FAIL"


def sort_attribution(
    attribution: dict[str, dict[str, float]],
    *,
    direction: str = "lower_is_worse",
) -> list[tuple[str, str, float]]:
    """Flatten ``{tag: {axis: delta}}`` and sort by worst delta first.

    `direction="lower_is_worse"` (default): negative deltas (e.g. quality
    drop) sort first.
    `direction="higher_is_worse"` (cost / latency): positive deltas first.
    """
    flat: list[tuple[str, str, float]] = []
    for tag, axes in attribution.items():
        for axis, delta in axes.items():
            flat.append((tag, axis, float(delta)))
    if direction == "higher_is_worse":
        flat.sort(key=lambda row: row[2], reverse=True)
    else:
        flat.sort(key=lambda row: row[2])
    return flat


def extract_axis_value(axis_name: str, record: dict[str, Any]) -> float:
    """Per-record value for a gate axis.

    Mirrors the extractors in :data:`evalgate.report.multi_axis.AXES` so the
    UI can compute per-case axis deltas without re-running the gate. ``latency_p95``
    pulls the raw ``latency_ms`` here — the p95 aggregation only matters at the
    axis level; for per-case ordering we want the raw values.
    """
    if axis_name == "latency_p95":
        return float(record.get("latency_ms", 0) or 0)
    if axis_name == "cost":
        return float(record.get("cost_usd", 0.0) or 0.0)
    # default: quality (or unknown -> score)
    return float(record.get("score", 0.0) or 0.0)


def _record_tags(record: dict[str, Any]) -> list[str]:
    raw = record.get("tags") or []
    if isinstance(raw, str):
        return [raw]
    return [str(t) for t in raw]


def per_case_axis_deltas(
    axis_name: str,
    baseline: Sequence[dict[str, Any]],
    candidate: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Pair baseline and candidate records by ``case_id`` and emit per-case rows.

    Each row carries the axis value on both sides, the signed delta, tags, and
    the candidate's ``eval_result_id`` (when present, so a future drill-down
    can deep-link). Cases present on only one side are skipped — the gate
    treats them as missing data, and we don't want to invent a baseline of 0.
    """
    b_by_id: dict[str, dict[str, Any]] = {}
    for r in baseline:
        cid = r.get("case_id")
        if cid is not None:
            b_by_id[str(cid)] = r

    rows: list[dict[str, Any]] = []
    for c in candidate:
        cid = c.get("case_id")
        if cid is None:
            continue
        b = b_by_id.get(str(cid))
        if b is None:
            continue
        b_val = extract_axis_value(axis_name, b)
        c_val = extract_axis_value(axis_name, c)
        rows.append(
            {
                "case_id": str(cid),
                "tags": _record_tags(c) or _record_tags(b),
                "baseline": b_val,
                "candidate": c_val,
                "delta": c_val - b_val,
                "eval_result_id": c.get("eval_result_id"),
            }
        )
    return rows


def top_regressed_cases(
    rows: Sequence[dict[str, Any]],
    *,
    axis_name: str,
    n: int = 5,
) -> list[dict[str, Any]]:
    """Sort ``per_case_axis_deltas`` rows worst-first and cap at ``n``.

    For higher-is-better axes (quality) "worst" means the most negative delta;
    for lower-is-better axes (cost / latency / safety) it's the most positive.
    Rows whose delta is in the "improved" direction are still included if they
    fit under the cap, so the table doesn't look empty when only some cases
    regressed — callers can filter with ``[r for r in ... if r["delta"] * sign > 0]``
    if they only want true regressions.
    """
    direction = AXIS_DIRECTION.get(axis_name, "higher_is_better")
    reverse = direction == "lower_is_better"
    sorted_rows = sorted(rows, key=lambda r: r["delta"], reverse=reverse)
    return sorted_rows[: max(n, 0)]


def per_tag_axis_deltas(
    axis_name: str,
    baseline: Sequence[dict[str, Any]],
    candidate: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Mean axis value per tag for both sides, sorted worst-first.

    Unlike the server-side ``tagwise_attribution`` (score-only), this computes
    attribution on **any** axis using its own extractor, which is what we want
    when surfacing why cost / latency / safety regressed. A record contributes
    to every tag it carries (matches the server-side behavior).
    """
    tags: set[str] = set()
    for r in (*baseline, *candidate):
        tags.update(_record_tags(r))

    out: list[dict[str, Any]] = []
    for tag in tags:
        b_vals = [extract_axis_value(axis_name, r) for r in baseline if tag in _record_tags(r)]
        c_vals = [extract_axis_value(axis_name, r) for r in candidate if tag in _record_tags(r)]
        if not b_vals and not c_vals:
            continue
        b_mean = sum(b_vals) / len(b_vals) if b_vals else 0.0
        c_mean = sum(c_vals) / len(c_vals) if c_vals else 0.0
        out.append(
            {
                "tag": tag,
                "baseline": b_mean,
                "candidate": c_mean,
                "delta": c_mean - b_mean,
                "n_baseline": len(b_vals),
                "n_candidate": len(c_vals),
            }
        )

    direction = AXIS_DIRECTION.get(axis_name, "higher_is_better")
    reverse = direction == "lower_is_better"
    out.sort(key=lambda r: r["delta"], reverse=reverse)
    return out


def format_run_label(run: dict[str, Any]) -> str:
    """Render a run dict as a one-line picker label.

    Used by the Reports page's `selectbox` so the user can tell two
    same-prompt runs apart by timestamp + candidate model.
    """
    ts = humanize_datetime(run.get("created_at"))
    prompt = run.get("prompt_path", "?")
    model = run.get("candidate_model", "?")
    score = run.get("mean_score")
    score_part = f" · score={humanize_score(score)}" if score is not None else ""
    return f"{ts} · {prompt} · {model}{score_part}"
