"""POST /v1/evals/run — runs the multi-axis gate over a baseline/candidate
pair and returns a GateReport. The judge runner that *produces* these eval
records lives behind this endpoint and is added in a follow-up.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from evalgate.core.schemas import GateReport
from evalgate.gate.decision import build_gate_report

router = APIRouter()


class EvalRunRequest(BaseModel):
    baseline: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Per-case eval records from the baseline (current main) run.",
    )
    candidate: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Per-case eval records from the candidate (PR head) run.",
    )


@router.post("/evals/run", response_model=GateReport)
async def run_evals(payload: EvalRunRequest) -> GateReport:
    return build_gate_report(payload.baseline, payload.candidate)
