"""The HMM state estimator (A16): a belief over focused / distracted / drowsy /
very_drowsy instead of a verdict.

Each update (about once a second):
 1. predict: the belief drifts over the time since the last update (continuous time:
    the same result whatever the camera's rate);
 2. flags: no usable data -> unknown; no face for a while -> absent. Both only drift,
    without evidence;
 3. update: every valid sign adds its log-likelihood, weighted for overlapping signs
    (correlation groups) and for how much new evidence a window holds (evidence time);
 4. report: the most likely family (focused / distracted / sleepy) with a 60/40 band
    against flicker, the confidence, and the signs that explain it.

Numbers: config/state_hmm.yaml. Guide: eepy/state/README.md ("The HMM"). Choices and
limits: docs/A16_HMM_DECISIONS.md. numpy + standard library, Python 3.9.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from . import hmm_model as model
from .config import fill
from .estimate import Reason, StateEstimate, register
from .hmm_params import HIDDEN, REPO_ROOT, FeatureSpec, HMMParams, load, sha256
from .hmm_prior import AppliedPrior, default as default_prior
from .rules import KEY_VALUES

FAMILIES = ("focused", "distracted", "sleepy")
FAMILY_OF_REPORTED = {"focused": 0, "distracted": 1, "drowsy": 2, "very_drowsy": 2}
# How tired each reported state is, for the drowsiness level (0 alert .. 1 very drowsy); an extra
# hidden state shown as focused (e.g. "tired") counts as a third.
DEPTH = {"focused": 0.0, "distracted": 0.0, "drowsy": 2 / 3, "very_drowsy": 1.0}
PHRASE = {"focused": "focus", "distracted": "distraction", "sleepy": "drowsiness"}
MIN_REASON = 0.1          # nats per full window: a sign that barely separates the states is no reason
STRENGTH_WORDS = ((0.5, "slightly suggests"), (1.5, "suggests"), (3.0, "clearly suggests"))


class Observation:
    """One valid sign at one step: what goes into the model, and what the text shows."""

    __slots__ = ("spec", "x", "value", "quiet", "uncalibrated", "show")

    def __init__(self, spec: FeatureSpec, x: Any, value: Any, quiet: bool, uncalibrated: bool,
                 show: str) -> None:
        self.spec, self.x, self.value, self.quiet = spec, x, value, quiet
        self.uncalibrated, self.show = uncalibrated, show


def _family_max(v: np.ndarray, groups: Optional[List[List[int]]] = None) -> np.ndarray:
    """Per family (focused, distracted, sleepy): the best of its hidden states."""
    if groups is None:                       # the four reported states
        return np.array([v[0], v[1], max(v[2], v[3])])
    return np.array([max(v[i] for i in g) for g in groups])


class HMMEstimator:
    name = "hmm"

    def __init__(self, params: Optional[HMMParams] = None, prior: Optional[AppliedPrior] = None) -> None:
        self.params = params if params is not None else load()
        p_ = self.params
        self.states, self.reports = p_.states, p_.reports
        fam = [FAMILY_OF_REPORTED[r] for r in self.reports]
        self._groups = [[i for i, f in enumerate(fam) if f == k] for k in range(3)]
        self._to_reported = np.zeros((len(self.states), len(HIDDEN)))
        for i, r in enumerate(self.reports):
            self._to_reported[i, HIDDEN.index(r)] = 1.0
        self._depth = np.array([DEPTH[r] if (s == r or r != "focused") else 1 / 3
                                for s, r in zip(self.states, self.reports)])
        self._q_base = model.generator(self.params.rates_per_hour)
        self.prior: AppliedPrior = default_prior(self.params)
        self.q = self._q_base
        self.set_session_prior(prior)

    # --- set-up ---------------------------------------------------------------------------------

    @property
    def gap_seconds(self) -> float:
        return self.params.gap_seconds

    def set_session_prior(self, prior: Optional[AppliedPrior]) -> None:
        """The prior of the current session (None: the hand-set values); used at every reset."""
        self.prior = prior if prior is not None else default_prior(self.params)
        lo, hi = self.params.prior_multiplier_bounds
        factor = min(max(self.prior.drowsiness_multiplier, lo), hi)
        self.q = (self._q_base if factor == 1.0
                  else model.generator(self.params.rates_per_hour, factor, self.params.drowsiness_pairs))
        self.reset()

    def reset(self) -> None:
        init = self._hidden_initial()
        self.log_b = np.log(init / init.sum())
        self.t_last: Optional[float] = None
        self.t_last_obs: Optional[float] = None
        self.obs_seconds = 0.0
        self.fam: Optional[int] = None
        self.very = False
        self.absent = False
        self.absent_since: Optional[float] = None
        self.was_flagged = False
        self.shown = "unknown"
        self.since: Optional[float] = None
        self.recent: Dict[str, np.ndarray] = {}

    def _hidden_initial(self) -> np.ndarray:
        """The prior's belief over the reported states, spread over the hidden states that show
        as each one in proportion to the parameter file's initial belief."""
        base = np.array(self.params.initial, dtype=float)
        if self.prior.source == "default" or len(self.states) == len(HIDDEN):
            if len(self.states) == len(HIDDEN):
                return np.array([self.prior.initial[s] for s in HIDDEN], dtype=float)
            return base
        out = np.zeros(len(self.states))
        for k, r in enumerate(HIDDEN):
            idx = [i for i, rr in enumerate(self.reports) if rr == r]
            share = base[idx] / base[idx].sum()
            out[idx] = self.prior.initial[r] * share
        return out

    def reported(self, hidden: np.ndarray) -> np.ndarray:
        """Hidden probabilities -> the four reported states."""
        return hidden @ self._to_reported

    def describe(self) -> Dict[str, Any]:
        """For the state_model event: which model and which prior run."""
        path = self.params.path
        try:
            digest = sha256(Path(path) if Path(path).is_absolute() else REPO_ROOT / path)
        except OSError:
            digest = None
        return {"estimator": self.name, "params": path, "version": self.params.version,
                "params_source": self.params.source, "sha256": digest,
                "features": [f.id for f in self.params.enabled_features],
                "states": list(self.states),
                "gap_seconds": self.params.gap_seconds, "prior": self.prior.as_dict()}

    # --- the signs ------------------------------------------------------------------------------

    def extract(self, f: Dict[str, Any]) -> Tuple[List[Observation], List[str]]:
        """The valid signs of this step, and what was skipped (and why). Nothing is filled in."""
        out: List[Observation] = []
        skipped: List[str] = []
        calibrated = bool(f.get("baseline_calibrated"))
        for spec in self.params.features:
            if not spec.enabled:
                continue
            failed = next((n for n in spec.needs if not n.test(f)), None)
            if failed is not None:
                have = f.get(failed.feature)
                if not (failed.feature == "au_status" and have is None):   # AUs off: silently
                    skipped.append(f"{spec.id}: needs {failed.text} (is {'missing' if have is None else have})")
                continue
            uncal = spec.calibration == "face" and not calibrated
            value = f.get(spec.input)
            show_template = spec.show
            fam = spec.family
            quiet = False
            if fam in ("student_t", "gaussian"):
                if uncal and spec.uncal_input is not None:
                    raw = f.get(spec.uncal_input)
                    if raw is None:
                        skipped.append(f"{spec.id}: no value for {spec.uncal_input}")
                        continue
                    value, x = raw, float(raw) / float(spec.uncal_divide or 1.0)
                    show_template = spec.show_uncalibrated or spec.show
                elif value is None:
                    skipped.append(f"{spec.id}: no value for {spec.input}")
                    continue
                else:
                    x = float(value)
                if spec.clip is not None:
                    x = min(max(x, spec.clip[0]), spec.clip[1])
                if spec.transform == "log":
                    x = math.log(x)
            elif fam == "bernoulli":
                if value is None and spec.missing_value is None:
                    skipped.append(f"{spec.id}: no value for {spec.input}")
                    continue
                x = int(bool(spec.event.test(f))) if value is not None and spec.event is not None \
                    else int(spec.missing_value or 0)
                quiet = x == 0
            elif fam == "poisson":
                if value is None:
                    skipped.append(f"{spec.id}: no value for {spec.input}")
                    continue
                exposure = f.get(spec.exposure) if isinstance(spec.exposure, str) else spec.exposure
                if exposure is None:
                    skipped.append(f"{spec.id}: no value for {spec.exposure}")
                    continue
                x = (int(round(float(value))), float(exposure) / 60.0)
                quiet = x[0] == 0
            else:   # beta, categorical
                if value is None:
                    skipped.append(f"{spec.id}: no value for {spec.input}")
                    continue
                x = float(value)
                if fam == "categorical":
                    quiet = model.bin_index(x, spec.bins) == 0
            if quiet and spec.show_zero:
                show_template = spec.show_zero
            out.append(Observation(spec, x, value, quiet, uncal, fill(show_template, value, None)))
        if not calibrated:
            skipped.append("not calibrated: eye signs at half weight")
        return out, skipped

    # --- one step -------------------------------------------------------------------------------

    def update(self, features: Dict[str, Any]) -> StateEstimate:
        p_ = self.params
        t = float(features["t"])
        dt = 0.0 if self.t_last is None else max(0.0, t - self.t_last)
        if self.since is None:
            self.since = t

        # 1 predict
        log_b = model.predict(self.log_b, model.transition(self.q, dt)) if dt > 0 else self.log_b
        if dt > 0 and self.recent:
            decay = math.exp(-dt / p_.recent_seconds)
            for key in self.recent:
                self.recent[key] = self.recent[key] * decay

        # 2 flags
        unknown_reason = self._unknown(features)
        if unknown_reason is None:
            self._absent_logic(features, t)
        absent = unknown_reason is None and self.absent

        # 3 update
        observations: List[Observation] = []
        skipped: List[str] = []
        contrib: Dict[str, Tuple[np.ndarray, np.ndarray, float]] = {}
        updated = False
        if unknown_reason is None and not absent:
            if self.was_flagged and self.t_last_obs is not None:
                # Restart evidence timing: the windows mostly cover the time before the gap.
                self.t_last_obs = t - min(dt, 1.0)
            observations, skipped = self.extract(features)
            dt_ev = 1.0 if self.t_last_obs is None else max(0.0, t - self.t_last_obs)
            weights = model.group_weights([o.spec for o in observations], p_.groups)
            total = np.zeros(len(self.states))
            for o in observations:
                ll = model.bounded(model.loglik(o.spec, o.x), p_.max_log_ratio)
                w = weights[o.spec.id] * (p_.uncalibrated_weight if o.uncalibrated else 1.0)
                c = w * min(dt_ev, o.spec.tau) / o.spec.tau * ll
                total += c
                contrib[o.spec.id] = (c, ll, w)
                self.recent[o.spec.id] = self.recent.get(o.spec.id, np.zeros(3)) + _family_max(c, self._groups)
            log_b = log_b + total
            self.t_last_obs = t
            self.obs_seconds += min(dt_ev, 5.0)
            updated = True
        self.was_flagged = unknown_reason is not None or absent

        # 4 normalise and report
        self.log_b = model.normalise(log_b, p_.min_prob)
        self.t_last = t
        hidden = np.exp(self.log_b)
        probs = self.reported(hidden)                        # focused, distracted, drowsy, very_drowsy
        pf = np.array([probs[0], probs[1], probs[2] + probs[3]])
        most_likely = HIDDEN[int(np.argmax(probs))]
        reported, pending = self._report(probs)
        if unknown_reason is not None:
            shown = "unknown"
        elif absent:
            shown = "absent"
        elif self.obs_seconds < p_.warmup_seconds:
            shown = "unknown"
            unknown_reason = Reason("starting_up", "unknown", f"starting up: {self.obs_seconds:.0f} s of data so far",
                                    "obs_seconds", round(self.obs_seconds, 3), p_.warmup_seconds)
        else:
            shown = reported
        if shown != self.shown:
            self.shown, self.since = shown, t
        family = self.fam if self.fam is not None else int(np.argmax(pf))
        confidence = float(pf[family])

        evidence = self._evidence(observations, contrib, probs) if updated else []
        if shown == "unknown":
            reasons = [unknown_reason] if unknown_reason is not None else []
        elif shown == "absent":
            reasons = [self._absent_reason(features)]
        else:
            reasons = self._reasons(observations, contrib, family, pf, probs, confidence, shown, pending)
        if updated and not observations:
            reasons = reasons or [Reason("hold", shown, "no usable signs this second")]
        say = self._say(reasons, observations)

        if unknown_reason is None and not features.get("no_data") and not features.get("gaze_calibrated") \
                and updated:
            skipped.append("gaze not calibrated: default screen and notes zones")
        values = {k: features[k] for k in KEY_VALUES if features.get(k) is not None}
        values.update(n_features=len(observations), params_version=p_.version,
                      drowsiness_level=round(float(hidden @ self._depth), 3))
        if len(self.states) != len(HIDDEN):
            values["hidden"] = {s: round(float(v), 4) for s, v in zip(self.states, hidden)}
        scores = {s: float(probs[i]) for i, s in enumerate(HIDDEN)}
        scores.update(absent=0.0, unknown=0.0)
        calibrated = bool(features.get("baseline_calibrated")) and bool(features.get("gaze_calibrated"))
        return StateEstimate(
            t=t, state=shown, scores=scores, reasons=reasons, calibrated=calibrated, skipped=skipped,
            since=self.since, pending=pending if shown not in ("unknown", "absent") else None,
            estimator=self.name, values=values, most_likely=most_likely,
            confidence=round(confidence, 6), uncertainty=round(model.entropy(probs), 3),
            evidence=evidence, updated=updated, say=say)

    # --- flags ----------------------------------------------------------------------------------

    def _unknown(self, f: Dict[str, Any]) -> Optional[Reason]:
        if f.get("no_data"):
            return Reason("no_data", "unknown", "no pictures from the camera")
        for check in self.params.unknown:
            ok = check.when.test(f)
            if ok or ok is None:                 # a missing value means no data either
                value = f.get(check.when.feature)
                text = fill(check.says, value, check.when.value) if ok else f"no {check.when.feature}"
                return Reason(check.id, "unknown", text, check.when.feature, value, check.when.value)
        return None

    def _absent_logic(self, f: Dict[str, Any], t: float) -> None:
        p_ = self.params
        if self.absent:
            if p_.absent_leave.test(f) is True:
                self.absent, self.absent_since = False, None
            return
        enter = p_.absent_enter.test(f)
        if enter is True:
            if self.absent_since is None:
                self.absent_since = t
            if t - self.absent_since >= p_.absent_enter_seconds:
                self.absent = True
        elif enter is False:
            self.absent_since = None

    def _absent_reason(self, f: Dict[str, Any]) -> Reason:
        cond = self.params.absent_enter
        value = f.get(cond.feature)
        match = re.match(r"^face_absent_ratio_(\d+)s$", cond.feature)
        if match and value is not None:
            text = f"no face for {float(value):.0%} of the last {match.group(1)} s"
        else:
            text = f"no face ({cond.text})"
        return Reason("no_face", "absent", text, cond.feature, value, cond.value)

    # --- reporting ------------------------------------------------------------------------------

    def _state_of(self, family: int, share: float, very: Optional[bool] = None) -> str:
        if family != 2:
            return FAMILIES[family]
        if very is None:
            very = share >= 0.5
        return "very_drowsy" if very else "drowsy"

    def _report(self, probs: np.ndarray) -> Tuple[str, Optional[str]]:
        """The reported state with hysteresis, and the pending state (the best family when it
        differs). Families switch at switch_probability, or when the current one falls below
        1 - switch_probability; within sleepy, very_drowsy has its own band."""
        p_ = self.params
        pf = [float(probs[0]), float(probs[1]), float(probs[2] + probs[3])]
        best = max(range(3), key=lambda k: (pf[k], -k))            # ties: focused, distracted, sleepy
        share = float(probs[3]) / max(pf[2], 1e-300)
        cur = self.fam
        entering = False
        if cur is None or (best != cur and (pf[best] >= p_.switch_probability
                                            or pf[cur] < 1.0 - p_.switch_probability)):
            entering = best != cur
            cur = best
        self.fam = cur
        if cur == 2:
            if entering:
                self.very = share >= 0.5
            elif not self.very and share >= p_.very_enter:
                self.very = True
            elif self.very and share <= p_.very_leave:
                self.very = False
        reported = self._state_of(cur, share, self.very)
        pending = None if best == cur else self._state_of(best, share)
        return reported, pending

    # --- explaining -----------------------------------------------------------------------------

    def _phrase(self, family: int, probs: np.ndarray) -> str:
        if family == 2 and probs[3] > probs[2]:
            return "strong drowsiness"
        return PHRASE[FAMILIES[family]]

    def _evidence(self, observations: List[Observation], contrib: Dict[str, Tuple[np.ndarray, np.ndarray, float]],
                  probs: np.ndarray) -> List[Dict[str, Any]]:
        """This step's signs, sorted by impact (nats moved in this step)."""
        items = []
        for o in observations:
            c, ll, w = contrib[o.spec.id]
            g, big = _family_max(c, self._groups), _family_max(ll, self._groups)
            order = sorted(range(3), key=lambda k: -g[k])
            supports = order[0] if g[order[0]] - g[order[1]] > 1e-9 else None
            against = order[2] if g[order[1]] - g[order[2]] > 1e-9 else None
            impact = float(g.max() - g.min())
            if supports is not None:
                strength = w * float(big[supports] - max(big[k] for k in range(3) if k != supports))
                words = next((wd for limit, wd in STRENGTH_WORDS if strength < limit), "strongly suggests")
                text = f"{o.spec.label} ({o.show}) {words} {self._phrase(supports, probs)}"
            elif against is not None:
                strength = w * float(max(big[k] for k in range(3) if k != against) - big[against])
                text = f"{o.spec.label} ({o.show}) speaks against {PHRASE[FAMILIES[against]]}"
            else:
                strength = 0.0
                text = f"{o.spec.label} ({o.show}) says nothing about the state"
            push = w * (big - big.mean())         # per full window, by family, centred (dt-free)
            items.append({"feature": o.spec.id, "input": o.spec.input, "value": o.value,
                          "supports": FAMILIES[supports] if supports is not None else None,
                          "against": FAMILIES[against] if against is not None else None,
                          "impact": round(impact, 3), "strength": round(strength, 3), "weight": round(w, 3),
                          "quiet": o.quiet, "text": text, "label": o.spec.label, "show": o.show,
                          "group": o.spec.group, "push": [round(float(v), 3) for v in push],
                          "step": [round(float(v - g.mean()), 4) for v in g]})
        items.sort(key=lambda d: -d["impact"])
        return items

    def _reasons(self, observations: List[Observation], contrib: Dict[str, Tuple[np.ndarray, np.ndarray, float]],
                 family: int, pf: np.ndarray, probs: np.ndarray, confidence: float, shown: str,
                 pending: Optional[str] = None) -> List[Reason]:
        """Why the shown state. A sign qualifies on what it says NOW: for focused, that it
        favours focused over the most likely other family (worded as a contrast: "eyes closed
        2% ... speaks against drowsiness"); for distracted and sleepy, that it favours that
        family over every other one, and is not "no news" (no yawns, looking at the work).
        Qualifying signs are ranked by their evidence over the last ~30 s against the most
        likely other family (dt-free), then by what they say now."""
        alt = max((k for k in range(3) if k != family), key=lambda k: (pf[k], -k))
        ranked = []
        for o in observations:
            c, ll, w = contrib[o.spec.id]
            g = _family_max(c, self._groups)
            if family == 0:
                if g[0] - g[alt] <= 1e-9:
                    continue
                text = f"{o.spec.label} ({o.show}) speaks against {PHRASE[FAMILIES[alt]]}"
            else:
                if o.quiet or any(g[family] - g[k] <= 1e-9 for k in range(3) if k != family):
                    continue
                text = f"{o.spec.label} ({o.show}) suggests {self._phrase(family, probs)}"
            big = _family_max(ll, self._groups)
            strength = w * float(big[family] - big[alt])
            if strength < MIN_REASON:
                continue
            r = self.recent.get(o.spec.id, np.zeros(3))
            ranked.append((float(r[family] - r[alt]), strength, o, text))
        ranked.sort(key=lambda m: (-m[0], -m[1]))
        reasons = [Reason(o.spec.id, shown, text, o.spec.input, o.value, None, None, round(margin, 3))
                   for margin, _, o, text in ranked[:self.params.evidence_top]]
        if not reasons:
            name = shown.replace("_", " ")
            if pending is not None:
                text = f"still {name} ({confidence:.0%} sure); {pending.replace('_', ' ')} is becoming more likely"
            elif family == 0:
                text = f"still {name} ({confidence:.0%} sure)"
            else:
                text = f"still {name} ({confidence:.0%} sure): no new signs of {self._phrase(family, probs)} right now"
            reasons = [Reason("hold", shown, text)]
        return reasons

    def _say(self, reasons: List[Reason], observations: List[Observation]) -> str:
        """The robot's {evidence}: the top reason's short value text, or "" (never the long sentence)."""
        if not reasons or reasons[0].rule in ("hold", "no_face", "no_data") or reasons[0].state in ("unknown",):
            return ""
        top = next((o for o in observations if o.spec.id == reasons[0].rule), None)
        return top.show if top is not None else ""


register("hmm", HMMEstimator)
