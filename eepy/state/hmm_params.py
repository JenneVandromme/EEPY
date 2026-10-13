"""Loading, checking and writing config/state_hmm.yaml: the HMM's numbers (A16).

Every number carries a `why` (where it comes from). A17 writes fitted parameters
in exactly this format, so `dump()` gives back what `load()` read (comments
aside: the `why` fields carry the reasons).

    params = load()                  # config/state_hmm.yaml, checked
    text = dump(params)              # canonical YAML; load(text) == params

Every mistake stops with a message saying what and where, e.g.

    config/state_hmm.yaml: features.perclos: unknown input "perclos_6s" (did you mean
    "perclos_60s"?). The features are listed in eepy/state/README.md.

pyyaml + standard library (both environments have pyyaml), Python 3.9.
"""

from __future__ import annotations

import copy
import difflib
import hashlib
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

from .config import Condition, parse_condition
from .features import WINDOWS, catalog, known

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATH = REPO_ROOT / "config" / "state_hmm.yaml"
FORMAT = "eepy.state_hmm/1"

HIDDEN = ("focused", "distracted", "drowsy", "very_drowsy")
FAMILIES = ("beta", "student_t", "gaussian", "poisson", "bernoulli", "categorical")
FAMILY_PARAMS = {"beta": ("mean", "concentration"), "student_t": ("mean", "sd"),
                 "gaussian": ("mean", "sd"), "poisson": ("rate_per_minute",),
                 "bernoulli": ("p",), "categorical": ("probs",)}
PRIOR_SOURCES = ("default", "heuristic", "kss", "llm")

# The canonical key order (dump() writes keys in this order).
TOP_ORDER = ("format", "version", "source", "created", "author", "description", "fit", "states",
             "initial", "transitions_per_hour", "groups", "features", "report", "absent", "unknown",
             "gap_seconds", "uncalibrated_weight", "numerics", "modulation", "session_prior")
FEATURE_ORDER = ("input", "family", "enabled", "df", "transform", "clip", "clip_why", "uncalibrated",
                 "exposure_seconds", "event", "event_why", "missing_value", "missing_value_why",
                 "bins", "bins_why", "group", "weight", "calibration", "evidence_seconds", "needs",
                 "needs_why", "label", "show", "show_zero", "show_uncalibrated", "params")
REQUIRED_FEATURE_KEYS = ("input", "family", "evidence_seconds", "calibration", "label", "show", "params")
VALUE_KEYS = ("value", "why", "prior_strength")
FIT_REQUIRED = ("method", "fitted_at", "sessions", "n_sessions", "n_seconds", "log_likelihood")


class HMMConfigError(ValueError):
    """config/state_hmm.yaml is wrong. The message is for a person."""


@dataclass(frozen=True)
class FeatureSpec:
    """One sign the HMM listens to, ready to use (numbers per hidden state in HIDDEN order)."""

    id: str
    input: str
    family: str
    tau: float                                  # evidence_seconds
    calibration: str                            # face | none
    label: str
    show: str
    params: Tuple[Dict[str, Any], ...]          # one per hidden state
    enabled: bool = True
    group: Optional[str] = None
    weight: float = 1.0
    needs: Tuple[Condition, ...] = ()
    transform: str = "none"
    clip: Optional[Tuple[float, float]] = None
    df: Optional[float] = None
    uncal_input: Optional[str] = None
    uncal_divide: Optional[float] = None
    exposure: Union[float, str, None] = None    # poisson: seconds, or a feature holding them
    event: Optional[Condition] = None
    missing_value: Optional[int] = None
    bins: Tuple[float, ...] = ()
    show_zero: Optional[str] = None
    show_uncalibrated: Optional[str] = None


@dataclass(frozen=True)
class UnknownCheck:
    id: str
    when: Condition
    says: str


@dataclass(frozen=True)
class HMMParams:
    """config/state_hmm.yaml, checked. `raw` is the canonical file content (what dump() writes);
    the other fields are the same numbers, ready to use."""

    raw: Dict[str, Any]
    version: int
    source: str
    initial: Tuple[float, ...]
    rates_per_hour: Tuple[Tuple[float, ...], ...]       # [from][to], 0 on the diagonal
    groups: Dict[str, float]                            # group -> correlation
    features: Tuple[FeatureSpec, ...]
    switch_probability: float
    very_enter: float
    very_leave: float
    warmup_seconds: float
    evidence_top: int
    recent_seconds: float
    absent_enter: Condition
    absent_enter_seconds: float
    absent_leave: Condition
    unknown: Tuple[UnknownCheck, ...]
    gap_seconds: float
    uncalibrated_weight: float
    min_prob: float
    max_log_ratio: float
    drowsiness_pairs: Tuple[Tuple[int, int], ...]       # the rates a drowsiness factor multiplies
    prior_enabled: bool
    prior_trust: Dict[str, float]
    prior_initial_bounds: Tuple[float, float]
    prior_multiplier_bounds: Tuple[float, float]
    path: str = field(default="config/state_hmm.yaml", compare=False)

    @property
    def enabled_features(self) -> Tuple[FeatureSpec, ...]:
        return tuple(f for f in self.features if f.enabled)

    def feature(self, fid: str) -> FeatureSpec:
        return next(f for f in self.features if f.id == fid)


# --- reading and writing --------------------------------------------------------------------


def _show(path: Path) -> str:
    try:
        return Path(path).resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(path)


def load(path: Optional[Union[str, Path]] = None, windows: Sequence[float] = WINDOWS) -> HMMParams:
    import yaml

    path = Path(path) if path is not None else DEFAULT_PATH
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise HMMConfigError(f"Cannot read {path}: {exc}") from None
    except yaml.YAMLError as exc:
        raise HMMConfigError(f"{_show(path)} is not valid YAML (check the indentation):\n{exc}") from None
    return parse(raw, where=_show(path), windows=windows)


def loads(text: str, where: str = "config/state_hmm.yaml") -> HMMParams:
    import yaml

    return parse(yaml.safe_load(text), where=where)


def dump(params: HMMParams) -> str:
    """The parameters as YAML, keys in the canonical order. load(dump(p)) == p."""
    import yaml

    return yaml.safe_dump(params.raw, sort_keys=False, allow_unicode=True, width=100)


def save(params: HMMParams, path: Union[str, Path]) -> None:
    Path(path).write_text(dump(params), encoding="utf-8")


def sha256(path: Optional[Union[str, Path]] = None) -> str:
    data = Path(path if path is not None else DEFAULT_PATH).read_bytes()
    return hashlib.sha256(data).hexdigest()


# --- checking -------------------------------------------------------------------------------


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _fmt(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, list):
        return "[" + ", ".join(_fmt(v) for v in value) + "]"
    return repr(value) if isinstance(value, str) else str(value)


def parse(raw: Any, where: str = "config/state_hmm.yaml", windows: Sequence[float] = WINDOWS) -> HMMParams:
    """Check a loaded YAML document; raise HMMConfigError with a message for a person."""

    def fail(message: str) -> HMMConfigError:
        return HMMConfigError(f"{where}: {message}")

    names = catalog(windows)

    def mapping(node: Any, label: str, what: str) -> Dict[str, Any]:
        if not isinstance(node, dict):
            raise fail(f"{label} must be {what}.")
        return node

    def only(node: Dict[str, Any], label: str, allowed: Sequence[str]) -> None:
        extra = [k for k in node if k not in allowed]
        if extra:
            raise fail(f"{label}: unknown setting(s) {sorted(map(str, extra))}; allowed: {', '.join(allowed)}.")

    def why(node: Dict[str, Any], label: str, key: str = "why") -> str:
        text = node.get(key)
        if not isinstance(text, str) or not text.strip():
            if key == "why":
                raise fail(f'{label} needs a "why" (one line: where the number comes from).')
            raise fail(f"{label}: {key[:-4]} needs a {key}.")
        return text

    def strength(node: Dict[str, Any], label: str) -> None:
        if "prior_strength" in node and not (_number(node["prior_strength"]) and node["prior_strength"] >= 0):
            raise fail(f"{label}.prior_strength must be a number >= 0 (pseudo-observations for A17), "
                       f"not {_fmt(node['prior_strength'])}.")

    def valued(node: Any, label: str, check: Optional[Callable[[float], Optional[str]]] = None,
               integer: bool = False) -> Tuple[float, Dict[str, Any]]:
        """A {value, why} number -> (value, canonical node)."""
        if not isinstance(node, dict):
            raise fail(f'{label} needs a "why" (one line: where the number comes from): '
                       f'write {{value: {_fmt(node)}, why: "..."}}.')
        only(node, label, VALUE_KEYS)
        value = node.get("value")
        if not _number(value) or (integer and not isinstance(value, int)):
            raise fail(f"{label} must be {'a whole number' if integer else 'a number'}, not {_fmt(value)}.")
        if check is not None:
            problem = check(float(value))
            if problem:
                raise fail(f"{label} must be {problem}, not {_fmt(value)}.")
        why(node, label)
        strength(node, label)
        return float(value), {k: node[k] for k in VALUE_KEYS if k in node}

    def condition(text: Any, label: str) -> Condition:
        return parse_condition(text, label, names, windows, fail)

    def feature_name(name: Any, label: str, what: str = "input") -> str:
        if not isinstance(name, str) or not known(name, names, windows):
            close = difflib.get_close_matches(str(name), list(names), n=1)
            hint = f' (did you mean "{close[0]}"?)' if close else ""
            raise fail(f'{label}: unknown {what} "{name}"{hint}. The features are listed in eepy/state/README.md.')
        return name

    def bounds(node: Dict[str, Any], key: str, label: str, lo: float, hi: float) -> Tuple[float, float]:
        pair = node.get(key)
        if (not isinstance(pair, list) or len(pair) != 2 or not all(_number(v) for v in pair)
                or not lo <= pair[0] < pair[1] <= hi):
            raise fail(f"{label}.{key} must be [low, high] with {_fmt(lo)} <= low < high <= {_fmt(hi)}, "
                       f"not {_fmt(pair)}.")
        why(node, label, f"{key}_why")
        return float(pair[0]), float(pair[1])

    if not isinstance(raw, dict):
        raise fail("expected the HMM parameters (format, states, initial, ...), found nothing usable.")
    only(raw, "the file", TOP_ORDER)
    if raw.get("format") != FORMAT:
        raise fail(f'format must be "{FORMAT}", not "{raw.get("format")}" (is this a parameter file '
                   "from another version?).")
    out: Dict[str, Any] = {"format": FORMAT}
    version = raw.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise fail(f"version must be a whole number >= 1 (bump it on every change), not {_fmt(version)}.")
    out["version"] = version
    source = raw.get("source")
    if source not in ("hand_set", "fitted"):
        raise fail(f'source must be hand_set or fitted, not "{source}".')
    out["source"] = source
    for key in ("created", "author", "description"):
        if key in raw:
            if not isinstance(raw[key], str):
                raise fail(f"{key} must be text.")
            out[key] = raw[key]

    # fit: written by A17
    fit = raw.get("fit")
    if source == "hand_set" and fit is not None:
        raise fail('source is "hand_set", so fit must be null (a fit: block belongs to fitted parameters).')
    if source == "fitted":
        if not isinstance(fit, dict) or any(k not in fit for k in FIT_REQUIRED):
            raise fail('source is "fitted", so a fit: block is needed (method, fitted_at, sessions, '
                       "n_sessions, n_seconds, log_likelihood).")
        sessions = fit["sessions"]
        if not isinstance(sessions, list) or not sessions:
            raise fail("fit.sessions must list the sessions the parameters were fitted on.")
        if fit["n_sessions"] != len(sessions):
            raise fail(f"fit.n_sessions is {_fmt(fit['n_sessions'])} but fit.sessions lists {len(sessions)}.")
        if not _number(fit["n_seconds"]) or fit["n_seconds"] <= 0:
            raise fail(f"fit.n_seconds must be a number > 0, not {_fmt(fit['n_seconds'])}.")
        if not _number(fit["log_likelihood"]):
            raise fail(f"fit.log_likelihood must be a number, not {_fmt(fit['log_likelihood'])}.")
    out["fit"] = copy.deepcopy(fit)

    states = raw.get("states")
    if states != list(HIDDEN):
        raise fail(f"states must be exactly {list(HIDDEN)}, not {states}.")
    out["states"] = list(HIDDEN)

    # initial
    init_raw = mapping(raw.get("initial"), "initial", "a probability per state")
    only(init_raw, "initial", HIDDEN)
    initial, init_out = [], {}
    for s in HIDDEN:
        if s not in init_raw:
            raise fail(f'initial: missing "{s}" (every state needs a starting probability).')
        v, node = valued(init_raw[s], f"initial.{s}", lambda x: None if 0 < x <= 1 else "between 0 and 1")
        initial.append(v)
        init_out[s] = node
    if abs(sum(initial) - 1.0) > 1e-6:
        raise fail(f"initial: the probabilities add up to {round(sum(initial), 6):g}; they must add up to 1.")
    out["initial"] = init_out

    # transitions
    trans_raw = mapping(raw.get("transitions_per_hour"), "transitions_per_hour", "rates per hour per pair of states")
    only(trans_raw, "transitions_per_hour", HIDDEN)
    rates = [[0.0] * 4 for _ in HIDDEN]
    trans_out: Dict[str, Any] = {}
    for i, a in enumerate(HIDDEN):
        row = trans_raw.get(a)
        if not isinstance(row, dict):
            raise fail(f'transitions_per_hour: missing "{a}" (every pair of different states needs a rate).')
        others = [b for b in HIDDEN if b != a]
        only(row, f"transitions_per_hour.{a}", others)
        trans_out[a] = {}
        for b in others:
            if b not in row:
                raise fail(f'transitions_per_hour.{a}: missing "{b}" (every pair of different states needs a rate).')
            label = f"transitions_per_hour.{a}.{b}"
            node = row[b]
            if isinstance(node, dict) and not (_number(node.get("value")) and node["value"] >= 0):
                raise fail(f"{label} must be a number >= 0 (times per hour), not {_fmt(node.get('value'))}.")
            v, node = valued(node, label)
            rates[i][HIDDEN.index(b)] = v
            trans_out[a][b] = node
    for problem in _unreachable(rates):
        raise fail(f"transitions_per_hour: {problem}")
    out["transitions_per_hour"] = trans_out

    # groups
    groups_raw = mapping(raw.get("groups") or {}, "groups", "group: {correlation: {value, why}} pairs")
    groups: Dict[str, float] = {}
    groups_out: Dict[str, Any] = {}
    for g, node in groups_raw.items():
        node = mapping(node, f"groups.{g}", "{correlation: {value, why}}")
        only(node, f"groups.{g}", ("correlation",))
        corr = node.get("correlation")
        if isinstance(corr, dict) and _number(corr.get("value")) and not 0 <= corr["value"] <= 1:
            raise fail(f"groups.{g}.correlation must be between 0 and 1, not {_fmt(corr['value'])}.")
        v, cnode = valued(corr, f"groups.{g}.correlation", lambda x: None if 0 <= x <= 1 else "between 0 and 1")
        groups[str(g)] = v
        groups_out[str(g)] = {"correlation": cnode}
    out["groups"] = groups_out

    # features
    feats_raw = mapping(raw.get("features"), "features", "the signs, by id")
    features: List[FeatureSpec] = []
    feats_out: Dict[str, Any] = {}
    for fid, entry in feats_raw.items():
        spec, canon = _feature(str(fid), entry, groups, fail, only, why, strength, valued, condition,
                               feature_name, mapping)
        features.append(spec)
        feats_out[str(fid)] = canon
    if not any(f.enabled for f in features):
        raise fail("features: at least one feature must be enabled.")
    out["features"] = feats_out

    # report
    rep = mapping(raw.get("report"), "report", "the reporting settings")
    only(rep, "report", ("switch_probability", "very_drowsy_share", "warmup_seconds", "evidence_top",
                         "recent_seconds"))
    rep_out: Dict[str, Any] = {}
    switch, rep_out["switch_probability"] = valued(rep.get("switch_probability"), "report.switch_probability",
                                                   lambda x: None if 0.5 < x < 1 else "between 0.5 and 1 (exclusive)")
    share = mapping(rep.get("very_drowsy_share"), "report.very_drowsy_share", "{enter, leave, why}")
    only(share, "report.very_drowsy_share", ("enter", "leave", "why"))
    enter, leave = share.get("enter"), share.get("leave")
    if not (_number(enter) and 0.5 <= enter <= 1 and _number(leave) and 0 <= leave <= 0.5):
        raise fail(f"report.very_drowsy_share needs 0.5 <= enter <= 1 and 0 <= leave <= 0.5, "
                   f"not enter {_fmt(enter)}, leave {_fmt(leave)}.")
    why(share, "report.very_drowsy_share")
    rep_out["very_drowsy_share"] = {"enter": enter, "leave": leave, "why": share["why"]}
    warmup, rep_out["warmup_seconds"] = valued(rep.get("warmup_seconds"), "report.warmup_seconds",
                                               lambda x: None if x >= 0 else "a number of seconds >= 0")
    top, rep_out["evidence_top"] = valued(rep.get("evidence_top"), "report.evidence_top",
                                          lambda x: None if 1 <= x <= 10 else "between 1 and 10", integer=True)
    recent, rep_out["recent_seconds"] = valued(rep.get("recent_seconds"), "report.recent_seconds",
                                               lambda x: None if x > 0 else "a number of seconds > 0")
    out["report"] = rep_out

    # absent
    ab = mapping(raw.get("absent"), "absent", "enter_when, enter_seconds and leave_when")
    only(ab, "absent", ("enter_when", "enter_when_why", "enter_seconds", "leave_when", "leave_when_why"))
    absent_enter = condition(ab.get("enter_when"), "absent.enter_when")
    why(ab, "absent", "enter_when_why")
    absent_seconds, ab_secs = valued(ab.get("enter_seconds"), "absent.enter_seconds",
                                     lambda x: None if x >= 0 else "a number of seconds >= 0")
    absent_leave = condition(ab.get("leave_when"), "absent.leave_when")
    why(ab, "absent", "leave_when_why")
    out["absent"] = {"enter_when": ab["enter_when"], "enter_when_why": ab["enter_when_why"],
                     "enter_seconds": ab_secs, "leave_when": ab["leave_when"],
                     "leave_when_why": ab["leave_when_why"]}

    # unknown
    unk_raw = raw.get("unknown") or []
    if not isinstance(unk_raw, list):
        raise fail("unknown must be a list of {id, when, says, why}.")
    unknown, unk_out = [], []
    for n, item in enumerate(unk_raw, 1):
        item = mapping(item, f"unknown entry {n}", "{id, when, says, why}")
        label = f'unknown "{item.get("id", n)}"'
        only(item, label, ("id", "when", "says", "why"))
        if not item.get("id") or not item.get("says"):
            raise fail(f"{label} needs an id and says.")
        when = condition(item.get("when"), label)
        _template(str(item["says"]), label, "says", fail)
        why(item, label)
        unknown.append(UnknownCheck(str(item["id"]), when, str(item["says"])))
        unk_out.append({"id": item["id"], "when": item["when"], "says": item["says"], "why": item["why"]})
    out["unknown"] = unk_out

    gap, out["gap_seconds"] = valued(raw.get("gap_seconds"), "gap_seconds",
                                     lambda x: None if x > 0 else "a number of seconds > 0")
    unc_w, out["uncalibrated_weight"] = valued(raw.get("uncalibrated_weight"), "uncalibrated_weight",
                                               lambda x: None if 0 <= x <= 1 else "between 0 and 1")
    num = mapping(raw.get("numerics"), "numerics", "min_prob and max_log_ratio")
    only(num, "numerics", ("min_prob", "max_log_ratio"))
    min_prob, mp = valued(num.get("min_prob"), "numerics.min_prob",
                          lambda x: None if 0 < x <= 0.01 else "between 0 and 0.01")
    max_lr, ml = valued(num.get("max_log_ratio"), "numerics.max_log_ratio",
                        lambda x: None if x > 0 else "a number > 0")
    out["numerics"] = {"min_prob": mp, "max_log_ratio": ml}

    # modulation: a stub in A16
    mod = mapping(raw.get("modulation"), "modulation", "the (switched off) time-of-day settings")
    only(mod, "modulation", ("enabled", "late_start_hour", "per_late_hour", "per_session_hour", "max_factor",
                             "applies_to", "applies_to_why"))
    if mod.get("enabled") is not False:
        if mod.get("enabled") is True:
            raise fail("modulation is not implemented yet (A16 keeps the slot; see eepy/state/README.md).")
        raise fail("modulation.enabled must be false.")
    mod_out: Dict[str, Any] = {"enabled": False}
    for key in ("late_start_hour", "per_late_hour", "per_session_hour", "max_factor"):
        _, mod_out[key] = valued(mod.get(key), f"modulation.{key}")
    applies = mod.get("applies_to")
    if not isinstance(applies, list) or not applies:
        raise fail('modulation.applies_to must list the rates a drowsiness factor changes, like "focused->drowsy".')
    pairs = []
    for item in applies:
        parts = str(item).split("->")
        if len(parts) != 2 or parts[0] not in HIDDEN or parts[1] not in HIDDEN or parts[0] == parts[1]:
            raise fail(f'modulation.applies_to: "{item}" is not a pair of two different states.')
        pairs.append((HIDDEN.index(parts[0]), HIDDEN.index(parts[1])))
    why(mod, "modulation", "applies_to_why")
    mod_out["applies_to"] = list(applies)
    mod_out["applies_to_why"] = mod["applies_to_why"]
    out["modulation"] = mod_out

    # session_prior (§16)
    sp = mapping(raw.get("session_prior"), "session_prior", "the per-session prior settings")
    only(sp, "session_prior", ("enabled", "trust", "initial_bounds", "initial_bounds_why",
                               "multiplier_bounds", "multiplier_bounds_why"))
    if not isinstance(sp.get("enabled"), bool):
        raise fail("session_prior.enabled must be true or false.")
    trust_raw = mapping(sp.get("trust"), "session_prior.trust", "a trust per source")
    only(trust_raw, "session_prior.trust", PRIOR_SOURCES)
    trust, trust_out = {}, {}
    for src in PRIOR_SOURCES:
        if src not in trust_raw:
            raise fail(f'session_prior.trust: missing "{src}".')
        trust[src], trust_out[src] = valued(trust_raw[src], f"session_prior.trust.{src}",
                                            lambda x: None if 0 <= x <= 1 else "between 0 and 1")
    ib = bounds(sp, "initial_bounds", "session_prior", 0.0, 1.0)
    if ib[0] * 4 > 1 or ib[1] * 4 < 1:
        raise fail("session_prior.initial_bounds must allow a uniform belief (low <= 0.25 <= high).")
    mb = bounds(sp, "multiplier_bounds", "session_prior", 0.0, math.inf)
    if not mb[0] > 0:
        raise fail("session_prior.multiplier_bounds: the low end must be > 0.")
    out["session_prior"] = {"enabled": sp["enabled"], "trust": trust_out,
                            "initial_bounds": list(sp["initial_bounds"]),
                            "initial_bounds_why": sp["initial_bounds_why"],
                            "multiplier_bounds": list(sp["multiplier_bounds"]),
                            "multiplier_bounds_why": sp["multiplier_bounds_why"]}

    canon = {k: out[k] for k in TOP_ORDER if k in out}
    return HMMParams(raw=canon, version=version, source=source, initial=tuple(initial),
                     rates_per_hour=tuple(tuple(r) for r in rates), groups=groups, features=tuple(features),
                     switch_probability=switch, very_enter=float(enter), very_leave=float(leave),
                     warmup_seconds=warmup, evidence_top=int(top), recent_seconds=recent,
                     absent_enter=absent_enter, absent_enter_seconds=absent_seconds, absent_leave=absent_leave,
                     unknown=tuple(unknown), gap_seconds=gap, uncalibrated_weight=unc_w, min_prob=min_prob,
                     max_log_ratio=max_lr, drowsiness_pairs=tuple(pairs), prior_enabled=sp["enabled"],
                     prior_trust=trust, prior_initial_bounds=ib, prior_multiplier_bounds=mb, path=where)


def _template(text: str, label: str, key: str, fail: Callable[[str], Exception]) -> None:
    try:
        text.format(value=0.5)
    except (ValueError, KeyError, IndexError) as exc:
        raise fail(f"{label}: `{key}` cannot be filled in ({exc}); use {{value}} only.") from None


def _unreachable(rates: List[List[float]]) -> List[str]:
    """Problems with the transition graph: a state no other state leads to, one that can
    never be left, or one not reachable from another."""
    n = len(rates)
    for j in range(n):
        if all(rates[i][j] <= 0 for i in range(n) if i != j):
            return [f"{HIDDEN[j]} can never be reached (all rates into it are 0)."]
    for i in range(n):
        if all(rates[i][j] <= 0 for j in range(n) if j != i):
            return [f"{HIDDEN[i]} can never be left (all rates out of it are 0)."]
    for start in range(n):
        seen, todo = {start}, [start]
        while todo:
            i = todo.pop()
            for j in range(n):
                if j not in seen and rates[i][j] > 0:
                    seen.add(j)
                    todo.append(j)
        missing = [HIDDEN[j] for j in range(n) if j not in seen]
        if missing:
            return [f"{missing[0]} can never be reached from {HIDDEN[start]}."]
    return []


def _feature(fid: str, entry: Any, groups: Dict[str, float], fail: Callable[[str], HMMConfigError],
             only: Any, why: Any, strength: Any, valued: Any, condition: Any, feature_name: Any,
             mapping: Any) -> Tuple[FeatureSpec, Dict[str, Any]]:
    label = f"features.{fid}"
    if not re.match(r"^[a-z][a-z0-9_]*$", fid):
        raise fail(f'{label}: an id is lower-case letters, digits and _ (like "perclos").')
    entry = mapping(entry, label, "a feature (input, family, params, ...)")
    only(entry, label, FEATURE_ORDER)
    for key in REQUIRED_FEATURE_KEYS:
        if key not in entry:
            raise fail(f"{label} needs `{key}`.")
    family = entry["family"]
    if family not in FAMILIES:
        raise fail(f'{label}: family "{family}" is not one of {", ".join(FAMILIES)}.')
    out: Dict[str, Any] = {}
    kw: Dict[str, Any] = {}
    inp = feature_name(entry["input"], label)
    enabled = entry.get("enabled", True)
    if not isinstance(enabled, bool):
        raise fail(f"{label}.enabled must be true or false.")
    tau, out["evidence_seconds"] = valued(entry["evidence_seconds"], f"{label}.evidence_seconds",
                                          lambda x: None if x > 0 else "a number of seconds > 0")
    calibration = entry["calibration"]
    if calibration not in ("face", "none"):
        raise fail(f'{label}.calibration must be face or none, not "{calibration}".')
    group = entry.get("group")
    if group is not None and group not in groups:
        raise fail(f'{label}.group "{group}" is not in groups (have: {", ".join(sorted(groups))}).')
    if "weight" in entry:
        kw["weight"], out["weight"] = valued(entry["weight"], f"{label}.weight",
                                             lambda x: None if x > 0 else "a number > 0")
    needs: Tuple[Condition, ...] = ()
    if "needs" in entry:
        if not isinstance(entry["needs"], list):
            raise fail(f"{label}.needs is a list, like [face_present_ratio_10s >= 0.5].")
        needs = tuple(condition(c, f"{label}.needs") for c in entry["needs"])
        why(entry, label, "needs_why")

    # family-specific settings
    transform = entry.get("transform", "none")
    if transform not in ("none", "log"):
        raise fail(f'{label}.transform must be none or log, not "{transform}".')
    if family not in ("student_t", "gaussian") and "transform" in entry and transform != "none":
        raise fail(f"{label}: transform is only for student_t and gaussian.")
    needs_clip = family == "beta" or transform == "log"
    if "clip" in entry or needs_clip:
        clip = entry.get("clip")
        if not needs_clip:
            raise fail(f"{label}: clip is only for beta, or with transform: log.")
        lo_ok = isinstance(clip, list) and len(clip) == 2 and all(_number(v) for v in clip)
        if family == "beta" and not (lo_ok and 0 < clip[0] < clip[1] < 1):
            raise fail(f"{label}.clip must be [low, high] with 0 < low < high < 1, not {_fmt(clip)}.")
        if family != "beta" and not (lo_ok and 0 < clip[0] < clip[1]):
            raise fail(f"{label}.clip must be [low, high] with 0 < low < high, not {_fmt(clip)}.")
        why(entry, label, "clip_why")
        kw["clip"] = (float(clip[0]), float(clip[1]))
    if family == "student_t":
        kw["df"], out["df"] = valued(entry.get("df"), f"{label}.df", lambda x: None if x > 0 else "a number > 0")
    elif "df" in entry:
        raise fail(f"{label}: df is only for student_t.")
    if "uncalibrated" in entry:
        if family not in ("student_t", "gaussian"):
            raise fail(f"{label}: uncalibrated is only for student_t and gaussian.")
        unc = mapping(entry["uncalibrated"], f"{label}.uncalibrated", "{input, divide_by: {value, why}}")
        only(unc, f"{label}.uncalibrated", ("input", "divide_by"))
        kw["uncal_input"] = feature_name(unc.get("input"), f"{label}.uncalibrated")
        kw["uncal_divide"], div = valued(unc.get("divide_by"), f"{label}.uncalibrated.divide_by",
                                         lambda x: None if x > 0 else "a number > 0")
        out["uncalibrated"] = {"input": unc["input"], "divide_by": div}
    if family == "poisson":
        exp = entry.get("exposure_seconds")
        if isinstance(exp, str):
            kw["exposure"] = feature_name(exp, f"{label}.exposure_seconds", "feature")
            out["exposure_seconds"] = exp
        else:
            kw["exposure"], out["exposure_seconds"] = valued(exp, f"{label}.exposure_seconds",
                                                             lambda x: None if x > 0 else "a number of seconds > 0")
    elif "exposure_seconds" in entry:
        raise fail(f"{label}: exposure_seconds is only for poisson.")
    if family == "bernoulli":
        kw["event"] = condition(entry.get("event"), f"{label}.event")
        why(entry, label, "event_why")
        if "missing_value" in entry:
            if entry["missing_value"] not in (0, 1) or isinstance(entry["missing_value"], bool):
                raise fail(f"{label}.missing_value must be 0 or 1, not {_fmt(entry['missing_value'])}.")
            kw["missing_value"] = int(entry["missing_value"])
            why(entry, label, "missing_value_why")
    elif "event" in entry or "missing_value" in entry:
        raise fail(f"{label}: event and missing_value are only for bernoulli.")
    if family == "categorical":
        bins = entry.get("bins")
        if not isinstance(bins, list) or not bins or not all(_number(b) for b in bins):
            raise fail(f"{label}.bins must be a list of numbers (the bin edges), like [0, 5, 10].")
        if bins[0] < 0 or any(b <= a for a, b in zip(bins, bins[1:])):
            raise fail(f"{label}.bins must increase: {_fmt(bins)}.")
        why(entry, label, "bins_why")
        kw["bins"] = tuple(float(b) for b in bins)
    elif "bins" in entry:
        raise fail(f"{label}: bins are only for categorical.")

    for key in ("label", "show"):
        if not isinstance(entry[key], str) or not entry[key].strip():
            raise fail(f"{label}.{key} must be text.")
    _template(entry["show"], label, "show", fail)
    for key in ("show_zero", "show_uncalibrated"):
        if key in entry:
            if not isinstance(entry[key], str):
                raise fail(f"{label}.{key} must be text.")
            _template(entry[key], label, key, fail)
            kw[key] = entry[key]

    # params per hidden state
    params_raw = mapping(entry["params"], f"{label}.params", "the numbers per state")
    only(params_raw, f"{label}.params", HIDDEN)
    keys = FAMILY_PARAMS[family]
    params, params_out = [], {}
    for s in HIDDEN:
        plabel = f"{label}.params.{s}"
        if s not in params_raw:
            raise fail(f"{label}.params: missing {s} (every state needs its numbers).")
        node = mapping(params_raw[s], plabel, "{" + ", ".join(keys) + ", why}")
        only(node, plabel, keys + ("why", "prior_strength"))
        for k in keys:
            if k not in node:
                raise fail(f"{plabel} needs {k}.")
        p = _family_params(family, node, plabel, kw.get("bins", ()), fail)
        why(node, plabel)
        strength(node, plabel)
        params.append(p)
        params_out[s] = {k: node[k] for k in keys + ("why", "prior_strength") if k in node}
    if family == "poisson" and not any(p["rate_per_minute"] > 0 for p in params):
        raise fail(f"{label}.params: at least one state needs a rate_per_minute > 0.")

    canon_src = dict(entry)
    canon_src.update(out)
    canon_src["params"] = params_out
    canon = {k: canon_src[k] for k in FEATURE_ORDER if k in canon_src}
    spec = FeatureSpec(id=fid, input=inp, family=family, tau=tau, calibration=calibration,
                       label=entry["label"], show=entry["show"], params=tuple(params), enabled=enabled,
                       group=group, needs=needs, transform=transform, **kw)
    return spec, canon


def _family_params(family: str, node: Dict[str, Any], label: str, bins: Tuple[float, ...],
                   fail: Callable[[str], Exception]) -> Dict[str, Any]:
    def num(key: str, ok: Callable[[float], bool], rule: str) -> float:
        value = node[key]
        if not _number(value) or not ok(value):
            raise fail(f"{label}: {key} must be {rule}, not {_fmt(value)}.")
        return float(value)

    if family == "beta":
        return {"mean": num("mean", lambda x: 0 < x < 1, "between 0 and 1 (exclusive)"),
                "concentration": num("concentration", lambda x: x > 0, "a number > 0")}
    if family in ("student_t", "gaussian"):
        return {"mean": num("mean", lambda x: True, "a number"), "sd": num("sd", lambda x: x > 0, "a number > 0")}
    if family == "poisson":
        return {"rate_per_minute": num("rate_per_minute", lambda x: x >= 0, "a number >= 0")}
    if family == "bernoulli":
        return {"p": num("p", lambda x: 0 < x < 1, "between 0 and 1 (exclusive)")}
    probs = node["probs"]
    if not isinstance(probs, list) or not all(_number(p) for p in probs):
        raise fail(f"{label}: probs must be a list of numbers.")
    if len(probs) != len(bins) + 1:
        raise fail(f"{label}: probs has {len(probs)} values; bins {_fmt(list(bins))} need {len(bins) + 1} "
                   "(one more than the edges).")
    if any(p <= 0 for p in probs):
        raise fail(f"{label}: every one of probs must be > 0 (nothing is impossible).")
    if abs(sum(probs) - 1) > 1e-6:
        raise fail(f"{label}: probs add up to {round(sum(probs), 6):g}; they must add up to 1.")
    return {"probs": [float(p) for p in probs]}


__all__ = ["DEFAULT_PATH", "FORMAT", "HIDDEN", "FAMILIES", "FeatureSpec", "HMMConfigError", "HMMParams",
           "UnknownCheck", "dump", "load", "loads", "parse", "save", "sha256"]
