"""The HMM's numbers (A16): pure functions, no state. numpy + standard library only
(no scipy at run time), Python 3.9.

    Q = generator(params.rates_per_hour)          # rates per second
    P = transition(Q, dt)                         # expm(Q dt), rows sum to 1
    log_b = predict(log_b, P)                     # the belief after dt seconds
    ll = loglik(spec, x)                          # one sign: ln p(x | state), per state
    log_b = normalise(log_b + evidence, min_prob)

`eepy/state/hmm.py` puts these together into the estimator; a later information-gain
module can use them to simulate "what if" beliefs without touching the live one.
"""

from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from .hmm_params import FeatureSpec

_LN_PI = math.log(math.pi)
_LN_2PI = math.log(2.0 * math.pi)


# --- transitions ----------------------------------------------------------------------------


def generator(rates_per_hour: Sequence[Sequence[float]], factor: float = 1.0,
              pairs: Iterable[Tuple[int, int]] = ()) -> np.ndarray:
    """The rate matrix Q (per second): off-diagonal rates, diagonal = -row sum. `factor`
    multiplies the rates in `pairs` (the rates into drowsiness)."""
    n = len(rates_per_hour)
    scaled = set(pairs)
    q = np.zeros((n, n))
    for i in range(n):
        for j in range(n):
            if i != j:
                q[i, j] = float(rates_per_hour[i][j]) / 3600.0 * (factor if (i, j) in scaled else 1.0)
        q[i, i] = -q[i].sum()
    return q


def transition(q: np.ndarray, dt: float) -> np.ndarray:
    """expm(Q dt) by scaling and squaring with a 12th-order Taylor series (Moler & Van Loan
    2003). Exact to ~1e-15 for these Q; rows clipped >= 0 and renormalised."""
    n = q.shape[0]
    if dt <= 0:
        return np.eye(n)
    a = q * float(dt)
    norm = float(np.abs(a).sum(axis=1).max())
    s = 0 if norm <= 0.5 else int(math.ceil(math.log2(norm / 0.5)))
    b = a / (2.0 ** s)
    e = np.eye(n)
    term = np.eye(n)
    for m in range(1, 13):
        term = term @ b / m
        e = e + term
    for _ in range(s):
        e = e @ e
    e = np.clip(e, 0.0, None)
    return e / e.sum(axis=1, keepdims=True)


def stationary(q: np.ndarray) -> np.ndarray:
    """The long-run distribution: pi Q = 0, sum 1."""
    n = q.shape[0]
    a = np.vstack([q.T, np.ones(n)])
    rhs = np.zeros(n + 1)
    rhs[-1] = 1.0
    return np.linalg.lstsq(a, rhs, rcond=None)[0]


# --- belief ---------------------------------------------------------------------------------


def logsumexp(v: np.ndarray) -> float:
    m = float(np.max(v))
    return m + math.log(float(np.exp(v - m).sum()))


def predict(log_b: np.ndarray, p: np.ndarray) -> np.ndarray:
    """ln of b P, in log space."""
    m = log_b.max()
    b = np.exp(log_b - m) @ p
    return np.log(np.maximum(b, 1e-300)) + m


def normalise(log_b: np.ndarray, min_prob: float = 0.0) -> np.ndarray:
    """Normalise to sum 1; no state below min_prob (renormalised after the floor)."""
    log_b = log_b - logsumexp(log_b)
    if min_prob > 0:
        log_b = np.maximum(log_b, math.log(min_prob))
        log_b = log_b - logsumexp(log_b)
    return log_b


def entropy(p: np.ndarray) -> float:
    """Shannon entropy divided by ln(n): 0 = certain, 1 = uniform (the BURTRAM convention)."""
    p = np.asarray(p, dtype=float)
    nz = p[p > 0]
    return float(-(nz * np.log(nz)).sum() / math.log(len(p)))


# --- emissions ------------------------------------------------------------------------------


def _beta(x: float, mean: float, conc: float) -> float:
    a, b = mean * conc, (1.0 - mean) * conc
    return ((a - 1.0) * math.log(x) + (b - 1.0) * math.log(1.0 - x)
            - (math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)))


def _student_t(y: float, mu: float, sd: float, nu: float) -> float:
    z = (y - mu) / sd
    return (math.lgamma((nu + 1.0) / 2.0) - math.lgamma(nu / 2.0) - 0.5 * (math.log(nu) + _LN_PI)
            - math.log(sd) - (nu + 1.0) / 2.0 * math.log1p(z * z / nu))


def _gaussian(y: float, mu: float, sd: float) -> float:
    z = (y - mu) / sd
    return -0.5 * z * z - math.log(sd) - 0.5 * _LN_2PI


def _poisson(k: int, mu: float) -> float:
    mu = max(mu, 1e-9)
    return k * math.log(mu) - mu - math.lgamma(k + 1.0)


def bin_index(x: float, edges: Sequence[float]) -> int:
    """0 if x <= e0; j if e_{j-1} < x <= e_j; len(edges) above the last edge."""
    for j, edge in enumerate(edges):
        if x <= edge:
            return j
    return len(edges)


def loglik(spec: FeatureSpec, x: Any) -> np.ndarray:
    """ln p(x | state) for each hidden state. `x` is the prepared observation:
    beta/student_t/gaussian a number (already transformed), poisson (count, minutes),
    bernoulli 0/1, categorical the raw value."""
    fam = spec.family
    out = np.empty(len(spec.params))
    if fam == "beta":
        lo, hi = spec.clip or (1e-6, 1 - 1e-6)
        v = min(max(float(x), lo), hi)
        for i, p in enumerate(spec.params):
            out[i] = _beta(v, p["mean"], p["concentration"])
    elif fam == "student_t":
        for i, p in enumerate(spec.params):
            out[i] = _student_t(float(x), p["mean"], p["sd"], float(spec.df or 3.0))
    elif fam == "gaussian":
        for i, p in enumerate(spec.params):
            out[i] = _gaussian(float(x), p["mean"], p["sd"])
    elif fam == "poisson":
        k, minutes = x
        for i, p in enumerate(spec.params):
            out[i] = _poisson(int(k), p["rate_per_minute"] * float(minutes))
    elif fam == "bernoulli":
        for i, p in enumerate(spec.params):
            out[i] = math.log(p["p"] if x else 1.0 - p["p"])
    elif fam == "categorical":
        j = bin_index(float(x), spec.bins)
        for i, p in enumerate(spec.params):
            out[i] = math.log(p["probs"][j])
    else:  # pragma: no cover -- the loader only allows the families above
        raise ValueError(f"unknown family {fam}")
    return out


def bounded(ll: np.ndarray, max_log_ratio: float) -> np.ndarray:
    """No state more than max_log_ratio nats below the best (one sign never rules a state out)."""
    if not math.isfinite(max_log_ratio):
        return ll
    return np.maximum(ll, ll.max() - max_log_ratio)


# --- double counting ------------------------------------------------------------------------


def group_weights(specs: Sequence[FeatureSpec], correlation: Dict[str, float]) -> Dict[str, float]:
    """Kish's effective sample size per correlation group: a group of n valid signs with
    correlation rho counts n / (1 + (n-1) rho) signs in total, shared by base weight."""
    weights: Dict[str, float] = {}
    members: Dict[str, List[FeatureSpec]] = {}
    for spec in specs:
        if spec.group is None:
            weights[spec.id] = spec.weight
        else:
            members.setdefault(spec.group, []).append(spec)
    for group, specs_in in members.items():
        n = len(specs_in)
        n_eff = n / (1.0 + (n - 1) * correlation.get(group, 0.0))
        total = sum(s.weight for s in specs_in)
        for s in specs_in:
            weights[s.id] = n_eff * s.weight / total
    return weights


def forward_step(log_b: np.ndarray, p: np.ndarray, evidence: Optional[np.ndarray],
                 min_prob: float) -> np.ndarray:
    """One full step: predict over dt, add the (weighted) log-evidence, normalise."""
    log_pred = predict(log_b, p)
    return normalise(log_pred if evidence is None else log_pred + evidence, min_prob)
