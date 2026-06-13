"""Group-sequential gate engine (Phase 15) — pure statistics, no DB / no LLM.

The fixed-N gate ([report/multi_axis.py](multi_axis.py)) only renders a verdict
*after* every case has been judged. This module lets the quality axis stop early:

- **early FAIL** via an alpha-spending boundary (Lan-DeMets approximation to
  O'Brien-Fleming or Pocock), so the cumulative false-fail rate stays <= alpha
  no matter how many interim looks we take;
- **early PASS** via stochastic curtailment: when the conditional power of ever
  crossing the fail boundary (under the worst tolerable regression drift) drops
  below ``gamma``, continuing is futile, so accept H0. Curtailment only ever
  *shortens* a run; it can never trigger a FAIL, so it leaves Type-I untouched.

The test is **paired**: baseline and candidate run the same ordered cases, so we
work on the per-case differences ``d_i = candidate_i - baseline_i``. On the
B-value scale ``B(t) = Z(t) * sqrt(t)`` the partial sums behave like Brownian
motion with independent normal increments under H0, which is exactly what the
boundary recursion needs.

Only numpy is available (no scipy), so the normal CDF uses ``math.erf`` and the
inverse-CDF uses Acklam's rational approximation.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum

import numpy as np

DEFAULT_ALPHA = 0.05
DEFAULT_LOOK_EVERY = 5
DEFAULT_MDE = 0.03
DEFAULT_GAMMA = 0.2
DEFAULT_SPENDING = "obf"

Spending = str  # "obf" | "pocock"


class Decision(StrEnum):
    """Verdict emitted at an interim look (or forced at the final look)."""

    continue_ = "continue"
    fail = "fail"
    pass_ = "pass"


# --------------------------------------------------------------------------- #
# Normal CDF / inverse-CDF (no scipy)
# --------------------------------------------------------------------------- #


def norm_cdf(x: float) -> float:
    """Standard normal CDF via ``math.erf``."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


# Acklam's inverse normal CDF rational approximation (|err| < 1.2e-9).
_A = (
    -3.969683028665376e1,
    2.209460984245205e2,
    -2.759285104469687e2,
    1.383577518672690e2,
    -3.066479806614716e1,
    2.506628277459239e0,
)
_B = (
    -5.447609879822406e1,
    1.615858368580409e2,
    -1.556989798598866e2,
    6.680131188771972e1,
    -1.328068155288572e1,
)
_C = (
    -7.784894002430293e-3,
    -3.223964580411365e-1,
    -2.400758277161838e0,
    -2.549732539343734e0,
    4.374664141464968e0,
    2.938163982698783e0,
)
_D = (
    7.784695709041462e-3,
    3.224671290700398e-1,
    2.445134137142996e0,
    3.754408661907416e0,
)


def norm_ppf(p: float) -> float:
    """Inverse standard normal CDF (Acklam's algorithm)."""
    if p <= 0.0:
        return -math.inf
    if p >= 1.0:
        return math.inf
    plow, phigh = 0.02425, 1.0 - 0.02425
    if p < plow:
        q = math.sqrt(-2.0 * math.log(p))
        return (((((_C[0] * q + _C[1]) * q + _C[2]) * q + _C[3]) * q + _C[4]) * q + _C[5]) / (
            (((_D[0] * q + _D[1]) * q + _D[2]) * q + _D[3]) * q + 1.0
        )
    if p <= phigh:
        q = p - 0.5
        r = q * q
        return (
            (((((_A[0] * r + _A[1]) * r + _A[2]) * r + _A[3]) * r + _A[4]) * r + _A[5])
            * q
            / (((((_B[0] * r + _B[1]) * r + _B[2]) * r + _B[3]) * r + _B[4]) * r + 1.0)
        )
    q = math.sqrt(-2.0 * math.log(1.0 - p))
    return -(((((_C[0] * q + _C[1]) * q + _C[2]) * q + _C[3]) * q + _C[4]) * q + _C[5]) / (
        (((_D[0] * q + _D[1]) * q + _D[2]) * q + _D[3]) * q + 1.0
    )


# --------------------------------------------------------------------------- #
# Alpha-spending functions
# --------------------------------------------------------------------------- #


def alpha_spent(spending: Spending, t: float, alpha: float = DEFAULT_ALPHA) -> float:
    """Cumulative alpha spent by information fraction ``t`` in ``[0, 1]``.

    Both functions satisfy ``f(0)=0``, ``f(1)=alpha`` and are monotone:

    - ``obf`` — Lan-DeMets O'Brien-Fleming-like: ``2(1 - Phi(z_{a/2}/sqrt(t)))``.
      Spends almost nothing early (very strict interim looks), most at the end.
    - ``pocock`` — ``alpha * ln(1 + (e-1) t)``. Spends more uniformly.
    """
    t = min(max(t, 0.0), 1.0)
    if t <= 0.0:
        return 0.0
    if spending == "obf":
        zc = norm_ppf(1.0 - alpha / 2.0)
        return 2.0 * (1.0 - norm_cdf(zc / math.sqrt(t)))
    if spending == "pocock":
        return alpha * math.log(1.0 + (math.e - 1.0) * t)
    raise ValueError(f"unknown spending function {spending!r}; expected 'obf' or 'pocock'")


# --------------------------------------------------------------------------- #
# Lan-DeMets lower-boundary recursion (Armitage-McPherson-Rowe)
# --------------------------------------------------------------------------- #


def _norm_pdf_grid(b: np.ndarray, sigma: float) -> np.ndarray:
    return np.exp(-0.5 * (b / sigma) ** 2) / (sigma * math.sqrt(2.0 * math.pi))


def _propagate(f: np.ndarray, dx: float, var: float) -> np.ndarray:
    """Convolve the sub-density ``f`` with a N(0, var) increment (B-value walk)."""
    sigma = math.sqrt(var)
    half = max(1, math.ceil(6.0 * sigma / dx))
    offsets = np.arange(-half, half + 1) * dx
    kernel = np.exp(-0.5 * (offsets / sigma) ** 2) / (sigma * math.sqrt(2.0 * math.pi))
    return np.convolve(f, kernel, mode="same") * dx


def compute_fail_boundaries(
    t_schedule: Sequence[float],
    spending: Spending = DEFAULT_SPENDING,
    alpha: float = DEFAULT_ALPHA,
    *,
    grid_limit: float = 8.0,
    grid_step: float = 0.005,
) -> list[float]:
    """Lower critical Z-values for each look so the cumulative false-fail == alpha.

    Walks the joint H0 density of the B-value process on a grid: at each look it
    finds the lower boundary ``l_k`` whose not-yet-crossed mass equals the
    incremental spend ``alpha*(t_k) - alpha*(t_{k-1})``, then truncates the
    crossed region before propagating to the next look. Returns the boundaries on
    the Z scale (``z_fail_k = l_k / sqrt(t_k)``); a regression is declared when
    the observed ``Z_k <= z_fail_k``.
    """
    t = [min(max(float(x), 1e-9), 1.0) for x in t_schedule]
    cum = [alpha_spent(spending, tk, alpha) for tk in t]
    incr: list[float] = []
    prev = 0.0
    for c in cum:
        incr.append(max(0.0, c - prev))
        prev = c

    b = np.arange(-grid_limit, grid_limit + grid_step, grid_step)
    z_fail: list[float] = []
    f: np.ndarray | None = None
    for k, tk in enumerate(t):
        f = _norm_pdf_grid(b, math.sqrt(tk)) if k == 0 else _propagate(f, grid_step, tk - t[k - 1])
        pi_k = incr[k]
        if pi_k <= 0.0:
            # Nothing to spend this look -> boundary at -inf (never fires).
            z_fail.append(-math.inf)
            continue
        cdf = np.cumsum(f) * grid_step
        idx = int(np.searchsorted(cdf, pi_k))
        idx = min(max(idx, 0), len(b) - 1)
        l_k = float(b[idx])
        z_fail.append(l_k / math.sqrt(tk))
        f[:idx] = 0.0  # truncate the crossed region for the next look
    return z_fail


def conditional_power(*, b_obs: float, l_final: float, t: float, mu_alt: float) -> float:
    """P(eventually cross the final boundary by t=1 | current B, drift mu_alt).

    Under the worst tolerable regression drift ``mu_alt`` (negative), the future
    increment to ``t=1`` is ``N(mu_alt*(1-t), 1-t)``. Low conditional power means
    even a real MDE-sized regression is now unlikely to fail -> futile -> PASS.
    """
    rem = 1.0 - t
    if rem <= 0.0:
        return 0.0
    return norm_cdf((l_final - b_obs - mu_alt * rem) / math.sqrt(rem))


# --------------------------------------------------------------------------- #
# Stateful sequential gate + pure replay
# --------------------------------------------------------------------------- #


@dataclass
class LookRecord:
    look: int
    n: int
    information_fraction: float
    z: float
    z_fail: float
    conditional_power: float
    decision: str


@dataclass
class SequentialOutcome:
    decision: str
    stopped_early: bool
    cases_consumed: int
    n_max: int
    spending: str
    mde: float
    gamma: float
    looks: list[LookRecord] = field(default_factory=list)


def _look_schedule(n_max: int, look_every: int) -> list[int]:
    look_every = max(1, look_every)
    ns = list(range(look_every, n_max + 1, look_every))
    if not ns or ns[-1] != n_max:
        ns.append(n_max)
    return [n for n in ns if n >= 1] or [n_max]


_SD_FLOOR = 1e-9


class SequentialGate:
    """Streaming quality-axis gate: feed paired diffs, get a verdict per look.

    Boundaries depend only on the look schedule + spending function, so they are
    computed once in ``__init__`` and reused as cases stream in. Call
    :meth:`update` once per paired case; it returns ``None`` between looks and a
    terminal :class:`Decision` (``fail``/``pass``) or ``continue`` at each look.
    """

    def __init__(
        self,
        *,
        n_max: int,
        look_every: int = DEFAULT_LOOK_EVERY,
        spending: Spending = DEFAULT_SPENDING,
        alpha: float = DEFAULT_ALPHA,
        mde: float = DEFAULT_MDE,
        gamma: float = DEFAULT_GAMMA,
    ):
        if n_max < 1:
            raise ValueError("n_max must be >= 1")
        self.n_max = n_max
        self.look_every = look_every
        self.spending = spending
        self.alpha = alpha
        self.mde = mde
        self.gamma = gamma
        self.look_ns = _look_schedule(n_max, look_every)
        self.t_schedule = [n / n_max for n in self.look_ns]
        self.z_fail = compute_fail_boundaries(self.t_schedule, spending, alpha)
        self.l_final = self.z_fail[-1] * math.sqrt(self.t_schedule[-1])
        self.looks: list[LookRecord] = []
        self.decision: Decision = Decision.continue_
        self._diffs: list[float] = []
        self._j = 0  # index into look_ns

    def update(self, diff: float) -> Decision | None:
        self._diffs.append(float(diff))
        n = len(self._diffs)
        if self._j >= len(self.look_ns) or n != self.look_ns[self._j]:
            return None
        decision, rec = self._evaluate_look(n)
        self.looks.append(rec)
        self._j += 1
        if decision in (Decision.fail, Decision.pass_):
            self.decision = decision
        return decision

    def _evaluate_look(self, n: int) -> tuple[Decision, LookRecord]:
        arr = np.asarray(self._diffs, dtype=np.float64)
        t = self.t_schedule[self._j]
        z_fail = self.z_fail[self._j]
        is_final = self._j == len(self.look_ns) - 1
        mean = float(arr.mean())
        sd = float(arr.std(ddof=1)) if n >= 2 else 0.0

        if sd <= 0.0:
            # Degenerate variance: identical diffs. Negative mean -> certain
            # regression; otherwise nothing to detect.
            if mean < 0.0:
                z, cp, decision = -math.inf, 1.0, Decision.fail
            else:
                z = math.inf if mean > 0.0 else 0.0
                cp = 0.0
                decision = Decision.pass_ if is_final else Decision.continue_
            return decision, self._record(n, t, z, z_fail, cp, decision)

        z = math.sqrt(n) * mean / sd
        b_obs = z * math.sqrt(t)
        if z <= z_fail:
            decision, cp = Decision.fail, 0.0
        elif is_final:
            decision, cp = Decision.pass_, 0.0
        else:
            mu_alt = -math.sqrt(self.n_max) * self.mde / max(sd, _SD_FLOOR)
            cp = conditional_power(b_obs=b_obs, l_final=self.l_final, t=t, mu_alt=mu_alt)
            decision = Decision.pass_ if cp < self.gamma else Decision.continue_
        return decision, self._record(n, t, z, z_fail, cp, decision)

    def _record(
        self, n: int, t: float, z: float, z_fail: float, cp: float, decision: Decision
    ) -> LookRecord:
        return LookRecord(
            look=self._j + 1,
            n=n,
            information_fraction=t,
            z=z,
            z_fail=z_fail,
            conditional_power=cp,
            decision=decision.value,
        )


def evaluate_sequential(
    baseline_scores: Sequence[float],
    candidate_scores: Sequence[float],
    *,
    look_every: int = DEFAULT_LOOK_EVERY,
    spending: Spending = DEFAULT_SPENDING,
    alpha: float = DEFAULT_ALPHA,
    mde: float = DEFAULT_MDE,
    gamma: float = DEFAULT_GAMMA,
) -> SequentialOutcome:
    """Replay two aligned score lists through the gate (Monte Carlo + smoke).

    Pairs by index (the lists are assumed ordered the same way) and feeds
    ``candidate - baseline`` diffs until a terminal decision or exhaustion. The
    final look always forces ``pass``/``fail``, so a decision is guaranteed.
    """
    n_max = min(len(baseline_scores), len(candidate_scores))
    if n_max < 1:
        raise ValueError("need at least one paired case")
    gate = SequentialGate(
        n_max=n_max,
        look_every=look_every,
        spending=spending,
        alpha=alpha,
        mde=mde,
        gamma=gamma,
    )
    decision: Decision | None = None
    consumed = 0
    for i in range(n_max):
        consumed = i + 1
        res = gate.update(candidate_scores[i] - baseline_scores[i])
        if res in (Decision.fail, Decision.pass_):
            decision = res
            break
    if decision is None:
        decision = gate.decision if gate.decision != Decision.continue_ else Decision.pass_
    return SequentialOutcome(
        decision=decision.value,
        stopped_early=consumed < n_max,
        cases_consumed=consumed,
        n_max=n_max,
        spending=spending,
        mde=mde,
        gamma=gamma,
        looks=gate.looks,
    )
