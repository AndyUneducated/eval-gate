"""POST /v1/evals/run — runs the eval suite and returns a multi-axis GateReport.

Walking-skeleton stub: returns a passing four-axis report so downstream CI
wiring can be exercised before the judge runner is implemented.
"""

from __future__ import annotations

from fastapi import APIRouter

from evalgate.core.schemas import AxisMetric, GateReport

router = APIRouter()


@router.post("/evals/run", response_model=GateReport)
async def run_evals() -> GateReport:
    return GateReport(
        passed=True,
        axes=[
            AxisMetric(name="quality", baseline=0.0, candidate=0.0, delta=0.0),
            AxisMetric(name="cost", baseline=0.0, candidate=0.0, delta=0.0),
            AxisMetric(name="latency_p95", baseline=0.0, candidate=0.0, delta=0.0),
            AxisMetric(name="safety", baseline=0.0, candidate=0.0, delta=0.0),
        ],
        summary="walking skeleton: judge runner not yet implemented",
    )
