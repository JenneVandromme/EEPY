"""The Explanation of one nudge decision (A20): faithful to what the model did, structured
for a spoken line.

    explainer = Explainer()
    explainer.feed(estimate, now)                     # every estimate the engine gets
    explanation = explainer.explain("bedtime", 3, "direct", now, window_start)

hmm mode -- from the HMM's evidence ledger (eepy/state/hmm.py), summed over the decision
window: for each evidence type, how much it moved log(b_decided / b_runner_up) between the
window's start and the decision. The items plus the drift ("dynamics") add up to the total
change exactly (`checks.sum_error`). Decided state: the ladder's family (refocus ->
distracted, bedtime -> drowsy or very drowsy); runner-up: the most likely other family.
Each family is represented by its most likely hidden state at the decision.

rules mode -- the rules that fired during the window, grouped by evidence type, with how
long each fired and its latest value (rules have no evidence strength: `contribution` is
None and items are ordered by seconds fired).

Same format in both modes. Plain words: docs/A20_EXPLANATIONS.md. Standard library only.
"""

from __future__ import annotations

from collections import deque
from typing import Any, Deque, Dict, List, Optional, Tuple

from .catalog import BY_ID, STATE_NAME, default_sentence, placeholder_values, type_of_input

FAMILIES = ("focused", "distracted", "sleepy")
FAMILY_OF = {"focused": "focused", "tired": "focused", "distracted": "distracted",
             "drowsy": "sleepy", "very_drowsy": "sleepy"}
LADDER_FAMILY = {"refocus": "distracted", "bedtime": "sleepy"}
FAMILY_WORD = {"focused": "focused", "distracted": "distracted", "sleepy": "drowsy"}
MIN_ITEM = 0.05            # nats: smaller contributions are not reasons
MIN_SHARE = 0.05           # ... nor are items under 5 % of the evidence (except the top one)
CONTRAST_SHARE = 0.2       # the contrast sentence names only items of at least 20 %
HISTORY_SECONDS = 1200.0   # how far back the explainer keeps estimates


def _family_state(state: Optional[str], ladder: str) -> str:
    if ladder == "bedtime":
        return state if state in ("drowsy", "very_drowsy") else "drowsy"
    return "distracted"


class Explainer:
    """Keeps the recent estimates (with the HMM's ledger, or the rules that fired) and builds
    the Explanation of a decision from them."""

    def __init__(self, top_k: int = 3, window_min: float = 60.0, window_max: float = 600.0) -> None:
        self.top_k, self.window_min, self.window_max = top_k, window_min, window_max
        self.history: Deque[Tuple[float, Dict[str, Any]]] = deque()

    def reset(self) -> None:
        self.history.clear()

    def feed(self, estimate: Dict[str, Any], now: float) -> None:
        self.history.append((now, estimate))
        while self.history and now - self.history[0][0] > HISTORY_SECONDS:
            self.history.popleft()

    # --- the decision ----------------------------------------------------------------

    def explain(self, ladder: str, level: int, tone: str, now: float, window_start: Optional[float],
                ambiguous: bool = False) -> Optional[Dict[str, Any]]:
        """The Explanation, or None when there is nothing to explain from (no estimates)."""
        if not self.history or ladder not in LADDER_FAMILY:
            return None
        start = now - self.window_min if window_start is None else window_start
        start = min(max(start, now - self.window_max), now - self.window_min)
        end_t, end = self.history[-1]
        if isinstance(end.get("ledger"), dict):
            out = self._hmm(ladder, end, start, now)
        else:
            out = self._rules(ladder, end, start, now)
        out.update(ladder=ladder, level=level, tone=tone, ambiguous=ambiguous,
                   confidence=end.get("confidence"), calibrated=end.get("calibrated"))
        self._words(out, tone)
        return out

    def _window(self, start: float) -> List[Tuple[float, Dict[str, Any]]]:
        return [(t, e) for t, e in self.history if t >= start]

    # --- hmm: the ledger ---------------------------------------------------------------

    def _hmm(self, ladder: str, end: Dict[str, Any], start: float, now: float) -> Dict[str, Any]:
        led_end = end["ledger"]
        epoch = led_end.get("epoch")
        same = [(t, e) for t, e in self.history
                if isinstance(e.get("ledger"), dict) and e["ledger"].get("epoch") == epoch]
        before = [(t, e) for t, e in same if t <= start]
        t0, first = before[-1] if before else same[0]
        led0 = first["ledger"]
        states: List[str] = list(led_end["states"])
        logb_end, logb0 = led_end["logb"], led0["logb"]

        def rep(family: str) -> int:
            idx = [i for i, s in enumerate(states) if FAMILY_OF.get(s, s) == family]
            return max(idx, key=lambda i: logb_end[i])

        def family_p(family: str) -> float:
            import math
            return sum(math.exp(logb_end[i]) for i, s in enumerate(states) if FAMILY_OF.get(s, s) == family)

        fam = LADDER_FAMILY[ladder]
        runner_fam = max((f for f in FAMILIES if f != fam), key=family_p)
        d, r = rep(fam), rep(runner_fam)
        values = end.get("values") or {}

        def delta(v_end: List[float], v0: Optional[List[float]], i: int) -> float:
            return v_end[i] - (v0[i] if v0 is not None else 0.0)

        items, negative = [], []
        ev_end, ev0 = led_end.get("ev") or {}, led0.get("ev") or {}
        for kind, v_end in ev_end.items():
            v0 = ev0.get(kind)
            c = delta(v_end, v0, d) - delta(v_end, v0, r)
            if c > 0:      # for the decided state
                items.append({"type": kind, "contribution": round(c, 4),
                              "direction": _direction(kind, fam, runner_fam, values)})
            else:          # for the runner-up
                negative.append({"type": kind, "contribution": round(c, 4),
                                 "direction": _direction(kind, runner_fam, fam, values)})
        drift = delta(led_end["drift"], led0["drift"], d) - delta(led_end["drift"], led0["drift"], r)
        total = (logb_end[d] - logb_end[r]) - (logb0[d] - logb0[r])
        evidence_sum = sum(e["contribution"] for e in items + negative)
        positive = sum(e["contribution"] for e in items) or 1.0
        items.sort(key=lambda e: -e["contribution"])
        negative.sort(key=lambda e: e["contribution"])
        for e in items:
            e["share"] = round(e["contribution"] / positive, 3)
        kept = [e for i, e in enumerate(items)
                if e["contribution"] >= MIN_ITEM and (i == 0 or e["share"] >= MIN_SHARE)][:self.top_k]
        window = self._window(t0)
        for e in kept:
            self._values(e, end, window, now - t0)
        despite = [e for e in negative if -e["contribution"] >= max(MIN_ITEM, CONTRAST_SHARE * positive)]
        decided = _family_state(end.get("state"), ladder)
        runner = states[r] if FAMILY_OF.get(states[r]) != "focused" else "focused"
        return {
            "mode": "hmm", "state": decided, "runner_up": runner,
            "window": {"start": round(t0, 3), "seconds": round(now - t0, 1), "steps": len(window)},
            "items": kept,
            "against": [e for e in negative if e["contribution"] <= -MIN_ITEM][:2],
            "dynamics": round(drift, 4),
            "contrast": {"runner_up": runner,
                         "because": [e["type"] for i, e in enumerate(kept[:2]) if i == 0 or e["share"] >= CONTRAST_SHARE],
                         "despite": [e["type"] for e in despite][:1],
                         "log_odds_change": round(total, 4)},
            "checks": {"log_odds_change": round(total, 4), "evidence": round(evidence_sum, 4),
                       "dynamics": round(drift, 4), "sum_error": round(total - evidence_sum - drift, 6),
                       "compared": [states[d], states[r]]},
        }

    # --- rules: what fired ----------------------------------------------------------

    def _rules(self, ladder: str, end: Dict[str, Any], start: float, now: float) -> Dict[str, Any]:
        window = self._window(start)
        fam_states = ("drowsy", "very_drowsy") if ladder == "bedtime" else ("distracted",)
        other_states = ("distracted",) if ladder == "bedtime" else ("drowsy", "very_drowsy")
        seen: Dict[str, Dict[str, Any]] = {}
        other: Dict[str, float] = {}
        prev_t = None
        for t, e in window:
            dt = 1.0 if prev_t is None else min(max(t - prev_t, 0.0), 2.0)
            prev_t = t
            fired = e.get("fired") or {}
            for state, reasons in fired.items():
                for reason in reasons:
                    kind = type_of_input(reason.get("feature"))
                    if kind is None:
                        continue                         # e.g. many_drowsy_signs: its parts are listed
                    if state in fam_states:
                        item = seen.setdefault(kind, {"type": kind, "contribution": None, "seconds": 0.0,
                                                      "rules": [], "direction": "for"})
                        item["seconds"] += dt
                        if reason["rule"] not in item["rules"]:
                            item["rules"].append(reason["rule"])
                        item["rule_value"] = {"rule": reason["rule"], "feature": reason.get("feature"),
                                              "value": reason.get("value"), "threshold": reason.get("threshold")}
                    elif state in other_states:
                        other[kind] = other.get(kind, 0.0) + dt
        items = sorted(seen.values(), key=lambda i: -i["seconds"])[:self.top_k]
        total = sum(i["seconds"] for i in seen.values()) or 1.0
        for i in items:
            i["seconds"] = round(i["seconds"], 1)
            i["share"] = round(i["seconds"] / total, 3)
            self._values(i, end, window, now - start)
        runner = ("distracted" if ladder == "bedtime" else "drowsy") if other else "focused"
        return {
            "mode": "rules", "state": _family_state(end.get("state"), ladder), "runner_up": runner,
            "window": {"start": round(start, 3), "seconds": round(now - start, 1), "steps": len(window)},
            "items": items, "against": [], "dynamics": None,
            "contrast": {"runner_up": runner, "because": [i["type"] for i in items[:2]],
                         "despite": sorted(other, key=lambda k: -other[k])[:1], "log_odds_change": None},
            "checks": {},
        }

    # --- values and words -------------------------------------------------------------

    @staticmethod
    def _values(item: Dict[str, Any], end: Dict[str, Any], window: List[Tuple[float, Dict[str, Any]]],
                seconds: float) -> None:
        """The values at the decision, the window's peaks, and the type's placeholders."""
        t = BY_ID.get(item["type"])
        values = dict(end.get("values") or {})
        if t is None:
            item.update(value={}, placeholders={"minutes": str(max(1, int(round(seconds / 60))))})
            return
        now_values = {k: values[k] for k in t.inputs if k in values}
        peaks: Dict[str, Any] = {}
        for _, e in window:
            for k in t.inputs:
                v = (e.get("values") or {}).get(k)
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    peaks[k] = max(peaks.get(k, v), v)
        item["value"] = now_values
        item["peak"] = {k: v for k, v in peaks.items() if v != now_values.get(k)}
        item["placeholders"] = placeholder_values(t.id, values, seconds)
        # the lead input: the measured input that best fits the type's sentences
        lead = next((tp.lead[0] for tp in t.templates["direct"]
                     if tp.lead and all(n in item["placeholders"]
                                        for n in _names(tp.text))), None)
        item["lead"] = lead

    @staticmethod
    def _words(out: Dict[str, Any], tone: str) -> None:
        items = out["items"]
        top = items[0] if items else None
        out["sentence"] = None
        out["text"] = None
        if top is not None and top["type"] in BY_ID:
            out["sentence"] = default_sentence(top["type"], tone, top.get("placeholders") or {}, top.get("lead"))
            out["text"] = "; ".join(_short(i) for i in items)
        c = out["contrast"]
        decided = STATE_NAME.get(out["state"], out["state"])
        runner = STATE_NAME.get(c["runner_up"], c["runner_up"])
        because = [_phrase(k, dict((i["type"], i) for i in items).get(k)) for k in c["because"]]
        because = [b for b in because if b]
        against = dict((i["type"], i) for i in out.get("against") or [])
        despite = [_phrase(k, against.get(k)) for k in c.get("despite") or []]
        despite = [d for d in despite if d]
        if out.get("ambiguous"):
            c["text"] = f"not clear whether {decided} or {runner}" + (f": {', and '.join(because)}" if because else "")
        elif because:
            c["text"] = f"{decided} rather than {runner}, because {' and '.join(because)}" + \
                (f", although {despite[0]}" if despite else "")
        else:
            c["text"] = f"{decided} rather than {runner}"


def _direction(kind: str, toward: str, other: str, values: Dict[str, Any]) -> str:
    """Did this evidence point to `toward` (a family) because its sign was there ("for": eyes
    closing), or because the sign of `other` was missing ("against": kept looking at the work)?
    From the type's usual meaning (the catalog), and for a type of a third state, from whether
    its sign was measured at the decision."""
    t = BY_ID.get(kind)
    if t is None:
        return "for"
    usual = FAMILY_OF.get(t.supports, t.supports)
    if usual == toward:
        return "for"
    if usual == other:
        return "against"
    present = set(placeholder_values(kind, values)) - {"minutes", "on_work_pct"}
    return "for" if present else "against"


def _names(text: str) -> List[str]:
    from .catalog import PLACEHOLDER
    return PLACEHOLDER.findall(text)


def _phrase(kind: str, item: Optional[Dict[str, Any]]) -> Optional[str]:
    t = BY_ID.get(kind)
    if t is None:
        return None
    return t.for_phrase if item is None or item.get("direction") != "against" else t.against_phrase


def _short(item: Dict[str, Any]) -> str:
    """ "eye closure 62 % (closed 34 % of the last minute)" -- for the panel and the log."""
    t = BY_ID.get(item["type"])
    title = t.title.lower() if t else item["type"].replace("_", " ")
    share = f" {item['share']:.0%}" if item.get("share") is not None else ""
    p = item.get("placeholders") or {}
    detail = []
    for key, fmt in (("closed_pct", "closed {}%"), ("times_normal", "blinks {}x normal"),
                     ("long_blinks", "{} long blinks"), ("longest_s", "longest {} s"), ("yawns", "{} yawns"),
                     ("nods", "{} nods"), ("away_pct", "away {}%"), ("away_seconds", "away now {} s")):
        if key in p:
            detail.append(fmt.format(p[key]))
    return f"{title}{share}" + (f" ({', '.join(detail[:2])})" if detail else "")
