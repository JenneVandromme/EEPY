"""The optional linear bandit (A24, config/bandit.yaml model: linear): for comparison, not the default.

One Bayesian linear regression per arm on the continuous context, reward = w . x + noise:
    x = [1, P(distracted), P(drowsy + very drowsy), confidence, calibrated, late evening, hours worked]
Prior w ~ N(mu0, prior_sd^2 I) with the intercept at the arm's prior mean (config prior.mean); known
noise (linear.noise). Thompson sampling draws w from the posterior and predicts. Only the evidence
(X'X, X'y) is stored, so changing the prior applies at once. Needs numpy.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Mapping

FEATURES = ("bias", "p_distracted", "p_sleepy", "confidence", "calibrated", "late_evening", "hours_worked")


def features(ctx: Mapping[str, Any]) -> List[float]:
    probs = ctx.get("probs") or {}
    if not probs and ctx.get("p_distracted") is not None:          # A22's snapshot spells them out
        probs = {k: ctx.get(f"p_{k}") or 0.0 for k in ("distracted", "drowsy", "very_drowsy")}
    hour = ctx.get("hour")
    late = 0.0 if hour is None else min(1.0, max(0.0, ((float(hour) - 21.0) % 24) / 3.0))
    worked = ctx.get("minutes_since_break")
    worked = ctx.get("minutes_in_session") if worked is None else worked
    conf = ctx.get("confidence")
    return [1.0, float(probs.get("distracted") or 0.0),
            float((probs.get("drowsy") or 0.0) + (probs.get("very_drowsy") or 0.0)),
            0.5 if conf is None else float(conf), 1.0 if ctx.get("calibrated") else 0.0, late,
            0.0 if worked is None else min(2.0, float(worked) / 60.0)]


class LinearModel:
    def __init__(self, config: Any, store: Dict[str, Any], prior_mean: Callable[[str], float]) -> None:
        import numpy as np

        self.np, self.config, self.store, self.prior_mean = np, config, store, prior_mean
        self.d = len(FEATURES)

    def _post(self, arm: str):
        np, d = self.np, self.d
        s2, n2 = self.config.linear_prior_sd ** 2, self.config.linear_noise ** 2
        mu0 = np.zeros(d)
        mu0[0] = self.prior_mean(arm)
        a0 = np.eye(d) / s2
        ev = self.store.get(arm) or {"xtx": [[0.0] * d for _ in range(d)], "xty": [0.0] * d, "n": 0}
        a = a0 + np.array(ev["xtx"]) / n2
        b = a0 @ mu0 + np.array(ev["xty"]) / n2
        cov = np.linalg.inv(a)
        return cov @ b, cov, ev["n"]

    def sample(self, arm: str, ctx: Mapping[str, Any], rng: Any) -> float:
        np = self.np
        mean, cov, _ = self._post(arm)
        gen = np.random.default_rng(int(rng.random() * 2 ** 32))
        w = gen.multivariate_normal(mean, cov)
        return float(max(-1.0, min(1.0, w @ np.array(features(ctx)))))

    def summary(self, arm: str, ctx: Mapping[str, Any]) -> Dict[str, Any]:
        np = self.np
        mean, cov, n = self._post(arm)
        x = np.array(features(ctx))
        return {"arm": arm, "mean": round(float(x @ mean), 4), "sd": round(float(np.sqrt(x @ cov @ x)), 4), "n": n}

    def update(self, arm: str, ctx: Mapping[str, Any], reward: float) -> None:
        d = self.d
        x = features(ctx)
        ev = self.store.setdefault(arm, {"xtx": [[0.0] * d for _ in range(d)], "xty": [0.0] * d, "n": 0})
        for i in range(d):
            ev["xty"][i] += x[i] * reward
            for j in range(d):
                ev["xtx"][i][j] += x[i] * x[j]
        ev["n"] += 1
