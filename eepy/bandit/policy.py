"""The contextual bandit with Thompson sampling (A24): it chooses WHICH nudge.

    policy = BanditPolicy(escalation_config, load_config(), store=Store())
    policy.start("P03")                         # this participant's posteriors (or the prior)
    action = policy.decide(context)             # the engine calls this when a nudge is due
    policy.observe(reward_event)                # A22's RewardEvent: the posterior learns

The engine (A14/A19) decides WHEN; A23's allowed_actions() decides what MAY be played; the bandit
picks inside that set. One Beta posterior per (bucket, arm) on the reward scaled to [0, 1]
(y = (r + 1) / 2: alpha += y, beta += 1 - y -- Thompson sampling for bounded rewards, Agrawal &
Goyal 2012). The prior is weak (config prior.strength observations) and centred so an untrained
bandit climbs the ladder like the fixed escalation. "Wait" has a fixed value: the bandit only
vetoes a nudge once it has learned that every allowed action does worse.

Arms are ladder levels ("refocus:2"); the tone follows A19's confidence rule (arms: levels), or
is part of the arm ("refocus:2:direct", arms: levels_and_tone). Buckets: refocus_early/late,
bedtime_early/late, ambiguous. Optional: model linear (Bayesian linear regression per arm on the
continuous context; numpy). Design: docs/A24_BANDIT.md.
"""

from __future__ import annotations

import json
import math
import random
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from eepy.interaction.policy import Action, NudgeContext, tone_for

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "config" / "bandit.yaml"
BUCKETS = ("refocus_early", "refocus_late", "bedtime_early", "bedtime_late", "ambiguous")
WAIT = "wait"
FORMAT = "eepy.bandit/1"
PROPENSITY_DRAWS = 200


class BanditConfigError(ValueError):
    pass


@dataclass(frozen=True)
class BanditConfig:
    model: str = "beta"
    arms: str = "levels"
    prior_strength: float = 8.0
    prior_mean: Dict[str, float] = field(default_factory=lambda: {"1": 0.6, "2": 0.25, "3": 0.0, "4": -0.2,
                                                                   "goodnight": -0.2})
    population: bool = False
    wait_value: float = -0.7
    wait_hold: float = 30.0
    learn_from_wizard: bool = False
    discount: float = 1.0
    late_work_minutes: float = 45.0
    late_hour: float = 23.0
    seed: Optional[int] = None
    linear_noise: float = 0.5
    linear_prior_sd: float = 0.5
    words: Dict[str, str] = field(default_factory=dict)
    path: str = "config/bandit.yaml"

    def as_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if k != "words"}


def load_config(path: Optional[Path] = None) -> BanditConfig:
    import yaml

    path = Path(path) if path is not None else CONFIG_PATH
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise BanditConfigError(f"{path}: {exc}") from None
    return parse(raw, _show(path))


def _show(path: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def parse(raw: Mapping[str, Any], where: str = "config/bandit.yaml") -> BanditConfig:
    def fail(msg: str) -> BanditConfigError:
        return BanditConfigError(f"{where}: {msg}")

    known = {"model", "arms", "prior", "wait", "learn_from_wizard", "discount", "buckets", "seed", "linear", "words"}
    if set(raw) - known:
        raise fail(f"unknown setting(s) {sorted(set(raw) - known)}; allowed: {sorted(known)}")

    def number(value: Any, label: str, low: float, high: float) -> float:
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not low <= value <= high:
            raise fail(f"{label} must be a number from {low} to {high}, not {value!r}")
        return float(value)

    d = BanditConfig()
    model = raw.get("model", d.model)
    if model not in ("beta", "linear"):
        raise fail(f"model must be beta or linear, not {model!r}")
    arms = raw.get("arms", d.arms)
    if arms not in ("levels", "levels_and_tone"):
        raise fail(f"arms must be levels or levels_and_tone, not {arms!r}")
    prior = raw.get("prior") or {}
    means = prior.get("mean", d.prior_mean)
    if not isinstance(means, dict) or not means:
        raise fail("prior.mean must give a mean reward per level (1: 0.6, 2: 0.2, ..., goodnight: -0.2)")
    prior_mean = {str(k): number(v, f"prior.mean.{k}", -1.0, 1.0) for k, v in means.items()}
    for k in prior_mean:
        if not (k.isdigit() or k == "goodnight"):
            raise fail(f"prior.mean: {k!r} is not a level (1, 2, ...) or goodnight")
    wait = raw.get("wait") or {}
    buckets = raw.get("buckets") or {}
    linear = raw.get("linear") or {}
    seed = raw.get("seed", None)
    if seed is not None and (not isinstance(seed, int) or isinstance(seed, bool)):
        raise fail(f"seed must be a whole number or null, not {seed!r}")
    words = raw.get("words") or {}
    if not isinstance(words, dict):
        raise fail("words must map ladder:level to a short description")
    for flag in ("learn_from_wizard",):
        if not isinstance(raw.get(flag, False), bool):
            raise fail(f"{flag} must be true or false")
    if not isinstance(prior.get("population", False), bool):
        raise fail("prior.population must be true or false")
    cfg = BanditConfig(
        model=model, arms=arms, prior_strength=number(prior.get("strength", d.prior_strength), "prior.strength", 0.5, 1000),
        prior_mean=prior_mean, population=bool(prior.get("population", False)),
        wait_value=number(wait.get("value", d.wait_value), "wait.value", -1.0, 1.0),
        wait_hold=number(wait.get("hold_seconds", d.wait_hold), "wait.hold_seconds", 0.0, 3600.0),
        learn_from_wizard=bool(raw.get("learn_from_wizard", False)),
        discount=number(raw.get("discount", d.discount), "discount", 0.5, 1.0),
        late_work_minutes=number(buckets.get("late_work_minutes", d.late_work_minutes), "buckets.late_work_minutes", 1, 600),
        late_hour=number(buckets.get("late_hour", d.late_hour), "buckets.late_hour", 12, 24),
        seed=seed, linear_noise=number(linear.get("noise", d.linear_noise), "linear.noise", 0.01, 5),
        linear_prior_sd=number(linear.get("prior_sd", d.linear_prior_sd), "linear.prior_sd", 0.01, 10),
        words={str(k): str(v) for k, v in words.items()}, path=where)
    if any(v <= cfg.wait_value for v in cfg.prior_mean.values()):
        raise fail(f"wait.value ({cfg.wait_value}) must be below every prior mean: an untrained bandit "
                   "should not veto the escalation")
    return cfg


# --- buckets and arms -----------------------------------------------------------------------


def bucket_of(ctx: Mapping[str, Any], cfg: BanditConfig) -> str:
    """The situation, from decision-time information only (a NudgeContext as a dict, or A22's
    context snapshot): refocus / bedtime / ambiguous, early or late."""
    if ctx.get("ambiguous"):
        return "ambiguous"
    state = ctx.get("state")
    ladder = ctx.get("ladder") or ("refocus" if state == "distracted" else "bedtime"
                                   if state in ("drowsy", "very_drowsy") else None)
    if ladder == "bedtime":
        hour = ctx.get("hour")
        late = hour is not None and (float(hour) >= cfg.late_hour or float(hour) < 5.0)
        return "bedtime_late" if late else "bedtime_early"
    worked = ctx.get("minutes_since_break")
    if worked is None:
        worked = ctx.get("minutes_in_session")
    late = worked is not None and float(worked) >= cfg.late_work_minutes
    return "refocus_late" if late else "refocus_early"


def context_dict(context: NudgeContext) -> Dict[str, Any]:
    """The NudgeContext in the same terms as A22's snapshot (minutes, hour)."""
    return {"state": context.state, "ladder": context.ladder, "ambiguous": context.ambiguous,
            "hour": context.hour,
            "minutes_in_session": context.session_seconds / 60.0,
            "minutes_since_break": None if context.since_break is None else context.since_break / 60.0,
            "confidence": context.confidence, "calibrated": context.calibrated,
            "probs": context.estimate.get("probs")}


def arm_of(ladder: Optional[str], level: Optional[int], tone: Optional[str], cfg: BanditConfig) -> str:
    if ladder is None or level is None:
        return WAIT
    return f"{ladder}:{level}:{tone}" if cfg.arms == "levels_and_tone" else f"{ladder}:{level}"


# --- the posterior store ----------------------------------------------------------------------


class Store:
    """Evidence per participant, in data/bandit/<participant>.json (numbers only; data/ is not in
    git). The prior is NOT stored: it is added from the config each time, so changing it applies."""

    def __init__(self, folder: Optional[Path] = None) -> None:
        if folder is None:
            from eepy.logs.session import DATA_DIR
            folder = DATA_DIR.parent / "bandit"
        self.folder = Path(folder)

    def path(self, participant: str) -> Path:
        safe = "".join(c for c in participant if c.isalnum() or c in "_-") or "default"
        return self.folder / f"{safe}.json"

    def participants(self) -> List[str]:
        if not self.folder.is_dir():
            return []
        return sorted(p.stem for p in self.folder.glob("*.json"))

    def load(self, participant: str) -> Dict[str, Any]:
        try:
            data = json.loads(self.path(participant).read_text(encoding="utf-8"))
            if data.get("format") == FORMAT:
                return data
        except (OSError, ValueError):
            pass
        return {"format": FORMAT, "participant": participant, "evidence": {}, "linear": {}, "updates": 0}

    def save(self, participant: str, data: Dict[str, Any]) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        data = dict(data, updated=time.strftime("%Y-%m-%d %H:%M:%S"))
        tmp = self.path(participant).with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path(participant))

    def reset(self, participant: str) -> bool:
        p = self.path(participant)
        if p.exists():
            p.unlink()
            return True
        return False

    def pooled(self, exclude: Optional[str] = None) -> Dict[str, Dict[str, List[float]]]:
        """Everyone's evidence added up (for the population prior)."""
        out: Dict[str, Dict[str, List[float]]] = {}
        for name in self.participants():
            if name == exclude:
                continue
            for bucket, arms in self.load(name).get("evidence", {}).items():
                for arm, (a, b, n) in arms.items():
                    acc = out.setdefault(bucket, {}).setdefault(arm, [0.0, 0.0, 0.0])
                    acc[0], acc[1], acc[2] = acc[0] + a, acc[1] + b, acc[2] + n
        return out


class MemoryStore(Store):
    """For simulations and tests: the same interface, nothing on disk."""

    def __init__(self) -> None:
        self.data: Dict[str, Dict[str, Any]] = {}
        self.folder = Path("(memory)")

    def participants(self) -> List[str]:
        return sorted(self.data)

    def load(self, participant: str) -> Dict[str, Any]:
        import copy
        return copy.deepcopy(self.data.get(participant) or
                             {"format": FORMAT, "participant": participant, "evidence": {}, "linear": {}, "updates": 0})

    def save(self, participant: str, data: Dict[str, Any]) -> None:
        import copy
        self.data[participant] = copy.deepcopy(data)

    def reset(self, participant: str) -> bool:
        return self.data.pop(participant, None) is not None


# --- the policy -------------------------------------------------------------------------------


class BanditPolicy:
    """Thompson sampling inside the allowed set. Thread-safe enough for the panel: decide() and
    observe() only touch plain dicts, and the engine serialises its own calls."""

    name = "bandit"

    def __init__(self, escalation: Any, config: Optional[BanditConfig] = None, store: Optional[Store] = None,
                 participant: str = "default", seed: Optional[int] = None) -> None:
        self.escalation = escalation
        self.config = config or BanditConfig()
        self.store = store if store is not None else MemoryStore()
        seed = seed if seed is not None else self.config.seed
        self.rng = random.Random(seed)
        self.last: Optional[Dict[str, Any]] = None        # the last decision's detail (the panel's bars)
        self.start(participant)

    # -- participant ------------------------------------------------------------------------

    def start(self, participant: str) -> None:
        """Load this participant's evidence; with the population prior, pool everyone else's."""
        self.participant = participant or "default"
        self.data = self.store.load(self.participant)
        self.pool = self.store.pooled(exclude=self.participant) if self.config.population else {}
        if self.config.model == "linear":
            from .linear import LinearModel
            self.linear = LinearModel(self.config, self.data.setdefault("linear", {}), self._prior_mean)

    # -- the prior --------------------------------------------------------------------------

    def _prior_mean(self, arm: str) -> float:
        if arm == WAIT:
            return self.config.wait_value
        ladder, level = arm.split(":")[0], int(arm.split(":")[1])
        if ladder == "bedtime" and level == getattr(self.escalation, "goodnight_level", 5):
            key = "goodnight"
        else:
            key = str(level)
        means = self.config.prior_mean
        if key not in means:                         # a level without its own value: the next lower one
            levels = sorted(int(k) for k in means if k.isdigit())
            key = str(max([lv for lv in levels if lv <= level] or levels[:1]))
        return means[key]

    def prior(self, bucket: str, arm: str) -> Tuple[float, float]:
        """(alpha0, beta0): `strength` pseudo-observations at the prior mean -- or, with the
        population prior, at everyone else's pooled posterior mean, scaled to the same strength."""
        s = self.config.prior_strength
        y = (self._prior_mean(arm) + 1.0) / 2.0
        pooled = self.pool.get(bucket, {}).get(arm)
        if pooled and pooled[2] > 0:
            y = (s * y + pooled[0]) / (s + pooled[0] + pooled[1])     # the pooled posterior mean
        return s * y, s * (1.0 - y)

    def posterior(self, bucket: str, arm: str) -> Tuple[float, float, float]:
        """(alpha, beta, n): the prior plus this participant's evidence."""
        a0, b0 = self.prior(bucket, arm)
        a, b, n = self.data["evidence"].get(bucket, {}).get(arm, [0.0, 0.0, 0.0])
        return a0 + a, b0 + b, n

    def summary(self, bucket: str, arm: str) -> Dict[str, Any]:
        """Mean and spread on the reward scale (-1..+1)."""
        if arm == WAIT:
            return {"arm": WAIT, "mean": self.config.wait_value, "sd": 0.0, "n": 0}
        a, b, n = self.posterior(bucket, arm)
        mean_y = a / (a + b)
        sd_y = math.sqrt(a * b / ((a + b) ** 2 * (a + b + 1.0)))
        return {"arm": arm, "mean": round(2 * mean_y - 1, 4), "sd": round(2 * sd_y, 4), "n": round(n, 2)}

    # -- choosing ---------------------------------------------------------------------------

    def _draw(self, bucket: str, arm: str, ctx: Dict[str, Any], rng: random.Random) -> float:
        if arm == WAIT:
            return self.config.wait_value
        if self.config.model == "linear":
            return self.linear.sample(arm, ctx, rng)
        a, b, _ = self.posterior(bucket, arm)
        return 2.0 * rng.betavariate(a, b) - 1.0

    def decide(self, context: NudgeContext) -> Action:
        allowed = [a for a in context.allowed if a.kind == "play"]
        ctx = context_dict(context)
        bucket = bucket_of(ctx, self.config)
        if not allowed:
            return Action.wait("nothing allowed")
        arms: Dict[str, List[Action]] = {}
        for a in allowed:
            arms.setdefault(arm_of(a.ladder, a.level, a.tone, self.config), []).append(a)
        names = list(arms) + [WAIT]
        samples = {arm: self._draw(bucket, arm, ctx, self.rng) for arm in names}
        chosen = max(names, key=lambda arm: (samples[arm], -names.index(arm)))
        propensity = self._propensity(bucket, names, ctx, chosen)
        detail = {
            "bucket": bucket, "model": self.config.model, "participant": self.participant, "chosen": chosen,
            "propensity": propensity,
            "arms": [dict(self.summary(bucket, arm) if self.config.model == "beta" else self.linear.summary(arm, ctx),
                          sample=round(samples[arm], 4), words=self.words(arm)) for arm in names]}
        self.last = dict(detail, t=context.now, sentence=self.sentence(bucket))
        if chosen == WAIT:
            return Action("wait", reason=f"bandit: in {bucket}, no allowed nudge is likely to help now",
                          propensity=propensity, extra={"hold": self.config.wait_hold, "bandit": detail})
        options = arms[chosen]
        if self.config.arms == "levels":
            tone = tone_for(context, self.escalation)
            pick = next((a for a in options if a.tone == tone), None) or next(a for a in options if a.tone == "hedged")
        else:
            pick = options[0]
        return replace(pick, reason=f"bandit: {self.words(chosen) or chosen} ({bucket}, sampled "
                                    f"{samples[chosen]:+.2f})", propensity=propensity, extra={"bandit": detail})

    def _propensity(self, bucket: str, names: List[str], ctx: Dict[str, Any], chosen: str) -> float:
        """The chance this choice had (re-drawn PROPENSITY_DRAWS times; for off-policy analysis)."""
        rng = random.Random(self.rng.random())          # its own draws, but reproducible from the seed
        wins = 0
        for _ in range(PROPENSITY_DRAWS):
            s = {arm: self._draw(bucket, arm, ctx, rng) for arm in names}
            wins += max(names, key=lambda arm: (s[arm], -names.index(arm))) == chosen
        return round(max(wins, 1) / PROPENSITY_DRAWS, 3)

    # -- learning ---------------------------------------------------------------------------

    def observe(self, event: Any) -> bool:
        """A22's RewardEvent (or its dict): update the posterior of the bucket at decision time.
        Returns whether it was used."""
        e = event if isinstance(event, Mapping) else event.as_dict()
        if not e.get("included") or e.get("reward") is None:
            return False                                  # censored: no fair verdict
        if e.get("source") == "wizard" and not self.config.learn_from_wizard:
            return False
        ctx = dict(e.get("context") or {})
        ctx.setdefault("ladder", e.get("ladder"))
        bucket = bucket_of(ctx, self.config)
        arm = arm_of(e.get("ladder"), e.get("level"), e.get("tone"), self.config)
        self.update(bucket, arm, float(e["reward"]), ctx)
        return True

    def update(self, bucket: str, arm: str, reward: float, ctx: Optional[Dict[str, Any]] = None) -> None:
        reward = max(-1.0, min(1.0, reward))
        if self.config.model == "linear":
            self.linear.update(arm, ctx or {}, reward)
        else:
            ev = self.data["evidence"].setdefault(bucket, {})
            g = self.config.discount
            if g < 1.0:                                   # old evidence fades: drift towards the prior
                for key in ev:
                    ev[key] = [v * g for v in ev[key]]
            a, b, n = ev.get(arm, [0.0, 0.0, 0.0])
            y = (reward + 1.0) / 2.0
            ev[arm] = [round(a + y, 6), round(b + 1.0 - y, 6), round(n + 1.0, 6)]
        self.data["updates"] = int(self.data.get("updates", 0)) + 1
        self.store.save(self.participant, self.data)

    # -- words ------------------------------------------------------------------------------

    def words(self, arm: str) -> str:
        if arm == WAIT:
            return "waiting"
        parts = arm.split(":")
        base = self.config.words.get(f"{parts[0]}:{parts[1]}", f"{parts[0]} level {parts[1]}")
        return base + (f" ({parts[2]})" if len(parts) > 2 else "")

    def sentence(self, bucket: str, min_n: float = 3.0) -> Optional[str]:
        """What the bandit currently believes, honestly worded (the posterior still includes the
        prior): "Right now EEPY expects the wordless glance to work best for you here (+0.42, 6 tries so far)." """
        if self.config.model != "beta":
            return None
        tried = [(self.summary(bucket, arm), arm) for arm in self.data["evidence"].get(bucket, {})]
        tried = [(s, arm) for s, arm in tried if s["n"] >= min_n]
        if not tried:
            return None
        s, arm = max(tried, key=lambda x: x[0]["mean"])
        verb = "has learned" if s["n"] >= self.config.prior_strength else "currently expects"
        return (f"EEPY {verb} that {self.words(arm)} works best for you here "
                f"(expected {s['mean']:+.2f}, {s['n']:.0f} tries so far).")

    def view(self, bucket: Optional[str] = None) -> Dict[str, Any]:
        """Every bucket's arms (posterior mean, spread, tries), for the launcher and `show`."""
        out: Dict[str, Any] = {"participant": self.participant, "updates": self.data.get("updates", 0),
                               "model": self.config.model, "buckets": {}}
        ladders = getattr(self.escalation, "ladders", {})
        for b in ([bucket] if bucket else BUCKETS):
            ladder = "bedtime" if b.startswith("bedtime") else "refocus"
            levels = list(ladders[ladder].levels) if ladder in ladders else [1, 2, 3, 4]
            if ladder == "bedtime":
                levels.append(getattr(self.escalation, "goodnight_level", 5))
            tones = ("hedged", "direct") if self.config.arms == "levels_and_tone" else (None,)
            arms = [arm_of(ladder, lv, t, self.config) for lv in levels for t in tones]
            out["buckets"][b] = [dict(self.summary(b, arm), words=self.words(arm)) for arm in arms]
            out["buckets"][b].append(dict(self.summary(b, WAIT), words="waiting"))
            out["sentences"] = out.get("sentences", {})
            out["sentences"][b] = self.sentence(b)
        return out
