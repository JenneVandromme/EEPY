"""The rule-based state estimator (A13): config/state_rules.yaml -> a state.

Each second:
 1. no data, or an `unknown` rule fires              -> unknown
 2. every rule is checked; drowsy rules add points; at points_needed, drowsy fires
 3. the first state in `priority` that fired           -> the RAW state (none: focused)
 4. dwell: the raw state must last `enter` seconds, and a milder state also needs
    `leave` seconds without signs of the current one, before the state changes.

Rules whose `needs` fail, or whose feature has no value, are SKIPPED (listed in
the estimate) -- they neither fire nor count against. See eepy/state/README.md.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .config import Rule, RulesConfig, fill, load
from .estimate import STATES, Reason, StateEstimate, register

KEY_VALUES = ("perclos_60s", "blink_mean_ms_60s", "blink_mean_ms_rel_60s", "long_blinks_60s",
              "yawns_60s", "nods_60s", "eyes_closed_now_s", "face_present_ratio_10s",
              "gaze_off_ratio_60s", "away_current_s", "down_other_ratio_10s", "AU43_active_60s",
              # also for the explanations' values (A20, eepy/explanations/catalog.py)
              "blink_max_ms_60s", "AU26_active_60s", "away_episodes_60s")


class RulesEstimator:
    name = "rules"

    def __init__(self, config: Optional[RulesConfig] = None) -> None:
        self.config = config or load()
        self.reset()

    @property
    def gap_seconds(self) -> float:
        return self.config.gap_seconds

    def describe(self) -> Dict[str, Any]:
        """For the state_model event."""
        from .config import REPO_ROOT

        path = Path(self.config.path)
        full = path if path.is_absolute() else REPO_ROOT / path
        try:
            digest: Optional[str] = hashlib.sha256(full.read_bytes()).hexdigest()
        except OSError:
            digest = None
        return {"estimator": self.name, "params": self.config.path, "sha256": digest,
                "gap_seconds": self.config.gap_seconds}

    def reset(self) -> None:
        self.state = "unknown"
        self.since: Optional[float] = None
        self._raw: Optional[str] = None
        self._raw_since = 0.0
        self._support = 0.0          # last time the raw state was the current one, or worse

    # --- one second ----------------------------------------------------------------------

    def evaluate(self, features: Dict[str, Any]) -> Tuple[str, Dict[str, List[Reason]], List[str], Dict[str, Any]]:
        """The raw state (no dwell), the reasons per state, what was skipped, key values."""
        cfg = self.config
        skipped: List[str] = []
        fired: Dict[str, List[Reason]] = {}
        values = {k: features[k] for k in KEY_VALUES if features.get(k) is not None}
        if features.get("no_data"):
            return "unknown", {"unknown": [Reason("no_data", "unknown", "no pictures from the camera")]}, \
                skipped, values
        for rule in cfg.unknown:
            ok = rule.when.test(features)
            if ok or ok is None:          # unknown: a missing value means no data either
                value = features.get(rule.when.feature)
                text = fill(rule.says, value, rule.when.value) if ok else f"no {rule.when.feature}"
                fired.setdefault("unknown", []).append(
                    Reason(rule.id, "unknown", text, rule.when.feature, value, rule.when.value))
        if "unknown" in fired:
            return "unknown", fired, skipped, values

        calibrated = bool(features.get("baseline_calibrated"))
        points: Dict[str, float] = {s: 0.0 for s in cfg.points_needed}
        later: List[Rule] = []
        for rule in cfg.rules:
            if rule.uses_points:
                later.append(rule)
                continue
            reason = self._check(rule, features, calibrated, skipped)
            if reason is None:
                continue
            if rule.state in points:
                points[rule.state] += rule.points or 0.0
            fired.setdefault(rule.state, []).append(reason)
        for state, total in points.items():
            features = dict(features, **{f"{state}_points": round(total, 3)})
            values[f"{state}_points"] = round(total, 3)
            if total < cfg.points_needed[state]:
                fired.pop(state, None)     # signs, but not enough of them yet
        for rule in later:
            reason = self._check(rule, features, calibrated, skipped)
            if reason is not None:
                fired.setdefault(rule.state, []).append(reason)
        raw = next((s for s in cfg.priority if s in fired), "focused")
        if raw == "focused":
            fired["focused"] = [Reason("no_signs", "focused",
                                       "face on the work; no signs of drowsiness or distraction")]
        return raw, fired, skipped, values

    def _check(self, rule: Rule, features: Dict[str, Any], calibrated: bool,
               skipped: List[str]) -> Optional[Reason]:
        for need in rule.needs:
            if not need.test(features):
                have = features.get(need.feature)
                skipped.append(f"{rule.id}: needs {need.text} (is {'missing' if have is None else have})")
                return None
        condition, says = rule.when, rule.says
        if features.get(condition.feature) is None and rule.uncalibrated is not None and not calibrated:
            condition, says = rule.uncalibrated, rule.says_uncalibrated or rule.says
        result = condition.test(features)
        if result is None:
            if not condition.feature.endswith("_points"):
                skipped.append(f"{rule.id}: no value for {condition.feature}")
            return None
        if not result:
            return None
        value = features.get(condition.feature)
        return Reason(rule.id, rule.state, fill(says, value, condition.value), condition.feature,
                      value, condition.value, rule.points)

    def update(self, features: Dict[str, Any]) -> StateEstimate:
        cfg = self.config
        t = float(features["t"])
        raw, fired, skipped, values = self.evaluate(features)
        if self.since is None:
            self.since = t
            self._support = t
        if raw != self._raw:
            self._raw, self._raw_since = raw, t
        if raw == self.state or (raw != "unknown" and cfg.severity(raw) > cfg.severity(self.state) >= 0):
            self._support = t
        pending: Optional[str] = None
        if raw != self.state:
            lasted = t - self._raw_since >= cfg.enter.get(raw, 0.0)
            milder = raw != "unknown" and cfg.severity(raw) < cfg.severity(self.state)
            calm = (t - self._support >= cfg.leave.get(self.state, 0.0)) if milder else True
            if lasted and calm:
                self.state, self.since = raw, t
                self._support = t
            else:
                pending = raw
        reasons = fired.get(self.state) or [self._hold_reason(t, pending)]
        calibrated = bool(features.get("baseline_calibrated")) and bool(features.get("gaze_calibrated"))
        if not features.get("no_data"):
            if not features.get("baseline_calibrated"):
                skipped.append("not calibrated: default eye and head values")
            if not features.get("gaze_calibrated"):
                skipped.append("gaze not calibrated: default screen and notes zones")
        return StateEstimate(t=t, state=self.state, scores={s: float(s == self.state) for s in STATES},
                             reasons=reasons, calibrated=calibrated, skipped=skipped,
                             since=self.since, pending=pending, estimator=self.name, values=values,
                             fired={k: v for k, v in fired.items() if k not in ("focused", "unknown")})

    def _hold_reason(self, t: float, pending: Optional[str]) -> Reason:
        """Why the state is still shown although its own signs are gone."""
        name = self.state.replace("_", " ")
        if pending is None:
            return Reason("hold", self.state, f"still {name}")
        wait_enter = self.config.enter.get(pending, 0.0) - (t - self._raw_since)
        wait_leave = self.config.leave.get(self.state, 0.0) - (t - self._support)
        if wait_leave > 0 and wait_leave >= wait_enter and self.state != "unknown":
            return Reason("hold", self.state, f"still {name} until {self.config.leave[self.state]:.0f} s "
                          f"without signs ({t - self._support:.0f} s so far)")
        return Reason("hold", self.state, f"{pending.replace('_', ' ')} in "
                      f"{max(0.0, wait_enter):.0f} s if it lasts")


register("rules", RulesEstimator)
