/* The /v5 page, part 1: small helpers and the EEPY design system's components as plain HTML.
   The markup is what the design system's own components render (DesignSystem.* in the canvas
   bundle), so the page looks like the proposal screens without React or a build step.
   Everything hangs off window.EE; the screens are in the other js/ files, the wiring in app.js. */
(function () {
  "use strict";

  var EE = window.EE = {};

  EE.$ = function (sel, el) { return (el || document).querySelector(sel); };
  EE.$$ = function (sel, el) { return Array.prototype.slice.call((el || document).querySelectorAll(sel)); };

  EE.esc = function (text) {
    return String(text == null ? "" : text).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  };
  var esc = EE.esc;

  // localStorage can be missing or blocked (private window): never let that break the page.
  EE.store = {
    get: function (key) { try { return window.localStorage.getItem(key); } catch (e) { return null; } },
    set: function (key, value) {
      try { if (value == null) window.localStorage.removeItem(key); else window.localStorage.setItem(key, value); } catch (e) { /* ignore */ }
    }
  };

  // Replace an element's HTML only when it changed: keeps focus, hover and running animations.
  EE.setHTML = function (el, html) { if (el && el.__html !== html) { el.innerHTML = html; el.__html = html; } };

  // --- the API ---------------------------------------------------------------------------------

  EE.getJSON = function (path) {
    return fetch(path, { cache: "no-store" }).then(function (r) { return r.json(); });
  };
  // {code, data}: for calls where a 404 means "this launcher is older than the page".
  EE.getCoded = function (path) {
    return fetch(path, { cache: "no-store" }).then(function (r) {
      return r.json().then(function (data) { return { code: r.status, data: data }; });
    });
  };
  EE.post = function (path, body) {
    return fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) })
      .then(function (r) { return r.json(); })
      .catch(function () { return { ok: false, message: "The launcher did not answer." }; });
  };

  // --- words and numbers -------------------------------------------------------------------------

  EE.cap = function (text) { text = String(text || "").replace(/_/g, " "); return text.charAt(0).toUpperCase() + text.slice(1); };
  EE.pct = function (x) { return x == null || isNaN(x) ? "–" : Math.round(x * 100) + "%"; };
  EE.minutes = function (sec) { return Math.round((sec || 0) / 60) + " min"; };
  EE.clockOf = function (sec) {
    sec = Math.max(0, Math.round(sec || 0));
    var h = Math.floor(sec / 3600), m = Math.floor(sec / 60) % 60, s = sec % 60;
    return (h ? h + ":" + String(m).padStart(2, "0") : String(m).padStart(2, "0")) + ":" + String(s).padStart(2, "0");
  };
  EE.hhmm = function (date) { return String(date.getHours()).padStart(2, "0") + ":" + String(date.getMinutes()).padStart(2, "0"); };
  // "21:07" for a moment `sec` seconds after a session that started at `clock` ("21:00").
  EE.addClock = function (clock, sec) {
    var m = /^(\d{1,2}):(\d{2})$/.exec(clock || "");
    if (!m) return "";
    var total = (parseInt(m[1], 10) * 60 + parseInt(m[2], 10) + Math.floor((sec || 0) / 60)) % 1440;
    return String(Math.floor(total / 60)).padStart(2, "0") + ":" + String(total % 60).padStart(2, "0");
  };

  // The panel's participant pattern (eepy/settings.py): a study code, never a name.
  EE.PARTICIPANT = /^P[0-9]{2,3}$/;
  EE.BACKENDS = [
    { value: "dry", label: "No robot", description: "I stay still. Good for a dry run." },
    { value: "sim", label: "Simulator", description: "A virtual me, in my own window." },
    { value: "real", label: "Real robot", description: "The real me. I'll ask for my address." }
  ];
  EE.MODES = [
    { value: "wizard", label: "You", description: "I only do what you press." },
    { value: "rules", label: "Simple rules", description: "I follow fixed if-then rules." },
    { value: "hmm", label: "I estimate", description: "I read how they're doing and climb a fixed ladder." },
    { value: "hmm_bandit", label: "I estimate and learn", description: "…and I learn what works for each person." }
  ];
  EE.labelOf = function (list, value) {
    for (var i = 0; i < list.length; i++) if (list[i].value === value) return list[i].label;
    return value || "";
  };

  EE.STATE_NAME = { focused: "Focused", tired: "Tired", distracted: "Distracted", drowsy: "Drowsy", very_drowsy: "Very drowsy",
                    absent: "Away from the desk", unknown: "Not sure yet" };
  // "P05 looks …": the highlighted word of the Right now panel.
  EE.STATE_LOOK = { focused: "focused", tired: "a bit tired", distracted: "distracted", drowsy: "tired", very_drowsy: "very tired",
                    absent: "away", unknown: "…" };
  // The moves, by their behaviour id: the names the operator sees (the robot's lines stay as in lines.yaml).
  EE.RUNG = {
    approving_nod: "Approving nod",
    refocus_study_buddy: "Just breathes", refocus_joint_attention: "Glance at notes", refocus_curious: "Curious tilt",
    refocus_verbal: "Spoken check", refocus_break_offer: "Offer a break",
    bedtime_companion: "Quiet company", bedtime_subtle: "Tired look", bedtime_attentive: "Yawn at you",
    bedtime_verbal: "Spoken check", bedtime_negotiate: "Wind-down deal", bedtime_goodnight: "Time to rest"
  };
  EE.LADDER_BEHAVIORS = {
    refocus: ["refocus_study_buddy", "refocus_joint_attention", "refocus_curious", "refocus_verbal", "refocus_break_offer"],
    bedtime: ["bedtime_companion", "bedtime_subtle", "bedtime_attentive", "bedtime_verbal", "bedtime_negotiate", "bedtime_goodnight"]
  };
  EE.behaviorName = function (id) { return EE.RUNG[id] || EE.cap(id); };
  EE.levelName = function (ladder, level) {
    var list = EE.LADDER_BEHAVIORS[ladder] || [];
    return list[level] ? EE.behaviorName(list[level]) : EE.cap(ladder) + " " + level;
  };
  // "refocus:2" / "refocus:2:direct" / "wait" (the bandit's arms) -> a move's name.
  EE.armName = function (arm, words) {
    if (arm === "wait") return "Waiting";
    if (words) return EE.cap(words);
    var p = String(arm || "").split(":");
    return p.length >= 2 ? EE.levelName(p[0], parseInt(p[1], 10)) + (p[2] === "direct" ? " (direct)" : "") : EE.cap(arm);
  };
  EE.MARKER = { user_went_to_bed: "Stopped to rest", user_replied: "User replied", user_dismissed: "Dismissed",
                user_accepted: "Accepted", user_snoozed: "Snoozed" };
  EE.OUTCOME = { success: "Worked", no_reaction: "No reaction", dismissed: "Dismissed", snoozed: "Snoozed",
                 interrupted_focus: "They were already focused", censored: "Not counted" };

  // --- design system components ----------------------------------------------------------------

  EE.icon = function (name, size) { return window.icon(name, size || 16); };

  EE.badge = function (tone, text) {
    tone = tone || "neutral";
    return '<span class="ds-badge ds-badge--' + tone + '">' + (tone !== "neutral" ? '<span class="ds-badge__dot" aria-hidden="true"></span>' : "") + text + "</span>";
  };

  // A Button: variant primary/secondary/danger, optional icon at the start or the end.
  EE.btn = function (variant, label, attrs, iconName, iconPos) {
    var glyph = iconName ? EE.icon(iconName, 16) : "";
    return '<button type="button" class="ds-btn ds-btn--' + variant + '"' + (attrs ? " " + attrs : "") + ">"
      + (iconPos === "start" ? glyph : "") + '<span class="ds-btn__label">' + label + "</span>" + (iconPos !== "start" ? glyph : "") + "</button>";
  };
  EE.linkBtn = function (variant, label, href, attrs, iconName, iconPos) {
    var glyph = iconName ? EE.icon(iconName, 16) : "";
    return '<a class="ds-btn ds-btn--' + variant + '" href="' + href + '"' + (attrs ? " " + attrs : "") + ">"
      + (iconPos === "start" ? glyph : "") + '<span class="ds-btn__label">' + label + "</span>" + (iconPos !== "start" ? glyph : "") + "</a>";
  };
  EE.iconBtn = function (label, iconName, attrs) {
    return '<button type="button" class="ds-iconbtn" aria-label="' + esc(label) + '" title="' + esc(label) + '"' + (attrs ? " " + attrs : "") + ">" + EE.icon(iconName, 18) + "</button>";
  };

  EE.textInput = function (o) {
    return '<div class="ds-field' + (o.error ? " is-error" : "") + '"' + (o.fieldId ? ' id="' + o.fieldId + '"' : "") + ">"
      + (o.label ? '<label class="ds-field__label" for="' + o.id + '">' + o.label + "</label>" : "")
      + '<div class="ds-field__control"><input id="' + o.id + '" class="ds-input" type="' + (o.type || "text") + '" spellcheck="false" autocomplete="off"'
      + (o.value != null ? ' value="' + esc(o.value) + '"' : "") + (o.placeholder ? ' placeholder="' + esc(o.placeholder) + '"' : "")
      + (o.hint ? ' aria-describedby="' + o.id + '-msg"' : "") + (o.attrs ? " " + o.attrs : "") + "></div>"
      + (o.hint != null ? '<p class="ds-field__msg" id="' + o.id + '-msg">' + o.hint + "</p>" : "") + "</div>";
  };

  EE.select = function (o) {
    return '<div class="ds-field">' + (o.label ? '<label class="ds-field__label" for="' + o.id + '">' + o.label + "</label>" : "")
      + '<div class="ds-field__control ds-select"><select id="' + o.id + '" class="ds-input ds-select__el"' + (o.attrs ? " " + o.attrs : "") + ">"
      + (o.options || []).map(function (opt) {
        return '<option value="' + esc(opt.value) + '"' + (String(opt.value) === String(o.value) ? " selected" : "") + ">" + esc(opt.label) + "</option>";
      }).join("") + "</select>" + EE.icon("ChevronRight", 16).replace('class="ds-icon"', 'class="ds-icon ds-select__chev"') + "</div>"
      + (o.hint ? '<p class="ds-field__msg">' + o.hint + "</p>" : "") + "</div>";
  };

  EE.radioGroup = function (o) {
    return '<fieldset class="ds-radio-group"><legend class="ds-field__label">' + o.label + "</legend>"
      + o.options.map(function (opt) {
        var id = o.name + "-" + opt.value;
        return '<div class="ds-check ds-radio"><input id="' + id + '" type="radio" name="' + o.name + '" value="' + opt.value + '" class="ds-check__input"'
          + (opt.value === o.value ? " checked" : "") + '><span class="ds-check__box" aria-hidden="true"><span class="ds-radio__dot"></span></span>'
          + '<label for="' + id + '" class="ds-check__label">' + opt.label + '<span class="ds-check__desc">' + opt.description + "</span></label></div>";
      }).join("") + "</fieldset>";
  };

  EE.switchEl = function (id, label, checked, attrs) {
    return '<div class="ds-switch"><button type="button" role="switch" id="' + id + '" aria-checked="' + (checked ? "true" : "false") + '" class="ds-switch__track"'
      + (attrs ? " " + attrs : "") + '><span class="ds-switch__thumb"></span></button><label for="' + id + '" class="ds-check__label">' + label + "</label></div>";
  };

  // PricingTabs: a pill tab group. Each button carries data-act / data-value for the wiring.
  EE.tabs = function (label, items, value, act) {
    return '<div role="tablist" aria-label="' + esc(label) + '" class="ds-tabs">' + items.map(function (it) {
      var on = it.value === value;
      return '<button type="button" role="tab" aria-selected="' + on + '" tabindex="' + (on ? 0 : -1) + '" class="ds-tab' + (on ? " is-selected" : "")
        + '" data-act="' + act + '" data-value="' + esc(it.value) + '">' + it.label + "</button>";
    }).join("") + "</div>";
  };

  EE.vizPanel = function (title, highlight, subtitle, body, attrs) {
    return '<section class="ds-viz"' + (attrs ? " " + attrs : "") + '><h2 class="ds-viz__title">' + title
      + (highlight ? ' <span class="ds-viz__hl">' + highlight + "</span>" : "") + "</h2>"
      + (subtitle ? '<p class="ds-viz__sub">' + subtitle + "</p>" : "") + '<div class="ds-viz__body">' + body + "</div></section>";
  };

  // FocusCurve: points are [fraction of the time 0-1, value 0-1, label]. Same curve and gradient as
  // the design system's component; the hover (cross, dot, tip) is wired once in app.js.
  var curveId = 0;
  EE.curves = {};
  EE.focusCurve = function (points, o) {
    o = o || {};
    var W = 1000, H = o.height || 200, pad = 28;
    var pts = (points || []).map(function (p) {
      return [Math.max(0, Math.min(1, p[0])) * W, H - pad / 2 - Math.max(0, Math.min(1, p[1])) * (H - pad * 1.5), p];
    });
    var line = function (list) {
      if (list.length < 2) return "";
      var d = "M" + list[0][0].toFixed(1) + " " + list[0][1].toFixed(1);
      for (var i = 0; i < list.length - 1; i++) {
        var p0 = list[i - 1] || list[i], p1 = list[i], p2 = list[i + 1], p3 = list[i + 2] || p2;
        var c1x = p1[0] + (p2[0] - p0[0]) / 6, c1y = p1[1] + (p2[1] - p0[1]) / 6, c2x = p2[0] - (p3[0] - p1[0]) / 6, c2y = p2[1] - (p3[1] - p1[1]) / 6;
        d += " C" + c1x.toFixed(1) + " " + Math.min(H, c1y).toFixed(1) + " " + c2x.toFixed(1) + " " + Math.min(H, c2y).toFixed(1) + " " + p2[0].toFixed(1) + " " + p2[1].toFixed(1);
      }
      return d;
    };
    var top = line(pts);
    var area = pts.length > 1 ? top + " L" + pts[pts.length - 1][0].toFixed(1) + " " + H + " L" + pts[0][0].toFixed(1) + " " + H + " Z" : "";
    var id = "fc" + (++curveId);
    EE.curves[id] = { pts: pts, W: W, H: H, label: o.label || "Focus" };
    return '<figure class="ds-curve"><div class="ds-curve__plot" data-curve="' + id + '"><svg viewBox="0 0 ' + W + " " + H + '" preserveAspectRatio="none" class="ds-curve__svg" role="img" aria-label="' + esc(o.aria || (o.label || "Focus") + " over the session") + '">'
      + '<defs><linearGradient id="' + id + 'g" x1="0" y1="1" x2="0" y2="0"><stop offset="0%" class="ds-curve__stop-low"></stop><stop offset="55%" class="ds-curve__stop-mid"></stop><stop offset="100%" class="ds-curve__stop-high"></stop></linearGradient></defs>'
      + '<path d="' + area + '" fill="url(#' + id + 'g)" class="ds-curve__area"></path><path d="' + top + '" class="ds-curve__line" vector-effect="non-scaling-stroke"></path>'
      + '<line class="ds-curve__cross" x1="0" x2="0" y1="0" y2="' + H + '" vector-effect="non-scaling-stroke" style="display: none;"></line></svg>'
      + '<span class="ds-curve__dot" style="display: none;"></span><span class="ds-curve__tip" role="tooltip" style="display: none;"></span></div>'
      + '<table class="ds-sr"><caption>' + esc(o.label || "Focus") + "</caption><tbody>" + (points || []).map(function (p) {
        return '<tr><th scope="row">' + esc(p[2] || "") + "</th><td>" + Math.round(p[1] * 100) + "%</td></tr>";
      }).join("") + "</tbody></table></figure>";
  };
  EE.curveHover = function (plot, clientX) {
    var c = EE.curves[plot.getAttribute("data-curve")];
    if (!c || !c.pts.length) return;
    var r = plot.getBoundingClientRect(), x = (clientX - r.left) / r.width * c.W;
    var best = c.pts.reduce(function (b, p) { return Math.abs(p[0] - x) < Math.abs(b[0] - x) ? p : b; }, c.pts[0]);
    var cross = plot.querySelector(".ds-curve__cross"), dot = plot.querySelector(".ds-curve__dot"), tip = plot.querySelector(".ds-curve__tip");
    cross.setAttribute("x1", best[0]); cross.setAttribute("x2", best[0]); cross.style.display = "";
    dot.style.display = ""; dot.style.left = best[0] / c.W * 100 + "%"; dot.style.top = best[1] / c.H * 100 + "%";
    tip.style.display = ""; tip.style.left = best[0] / c.W * 100 + "%";
    tip.textContent = (best[2][2] ? best[2][2] + " · " : "") + c.label + " " + Math.round(best[2][1] * 100) + "%";
  };
  EE.curveLeave = function (plot) {
    ["ds-curve__cross", "ds-curve__dot", "ds-curve__tip"].forEach(function (k) { var el = plot.querySelector("." + k); if (el) el.style.display = "none"; });
  };

  // DayTimeline (small, no ends): segments {a, b, type, label} and pins {at, label, icon} in percent
  // of the span, ticks in percent, start and end labels.
  EE.dayTimeline = function (o) {
    var segs = o.segments || [];
    return '<div class="ds-tl ds-tl--' + (o.size || "sm") + ' no-ends" role="group" aria-label="' + esc(o.aria || "") + '">'
      + '<div class="ds-tl__above" aria-hidden="true">' + (o.pins || []).map(function (p) {
        return '<span class="ds-tl__pin" style="left: ' + p.at.toFixed(2) + '%;" title="' + esc(p.label) + '">' + EE.icon(p.icon || "Play", 14) + "</span>";
      }).join("") + "</div>"
      + '<div class="ds-tl__track">' + segs.map(function (s) {
        return '<div class="ds-tl__seg ds-tl__seg--' + s.type + '" style="left: calc(' + s.a.toFixed(2) + '% + 2px); width: calc(' + Math.max(0.6, s.b - s.a).toFixed(2) + '% - 4px);" title="' + esc(s.label || "") + '"></div>';
      }).join("") + "</div>"
      + '<div class="ds-tl__ticks" aria-hidden="true">' + (o.ticks || []).map(function (t) {
        return '<span class="ds-tl__tick" style="left: ' + t.toFixed(2) + '%;"></span>';
      }).join("") + "</div>"
      + '<div class="ds-tl__labels" aria-hidden="true"><span class="ds-tl__label ds-tl__label--wake" style="left: 0%;">' + esc(o.start || "") + "</span>"
      + '<span class="ds-tl__label ds-tl__label--sleep" style="left: 100%;">' + esc(o.end || "") + "</span></div></div>";
  };

  // The design system's Modal, on its scrim. Wired by app.js (data-act="modal-close").
  EE.modal = function (title, desc, body, actions, img) {
    return '<div class="ds-modal" role="dialog" aria-modal="true" aria-labelledby="m-title">'
      + '<div class="ds-modal__head">' + (img ? '<img class="ds-modal__img" src="' + img + '" alt="">' : "")
      + '<h2 class="ds-modal__title" id="m-title">' + title + "</h2>"
      + '<button type="button" class="ds-iconbtn ds-modal__close" data-act="modal-close" aria-label="Close" title="Close">' + EE.icon("X", 18) + "</button></div>"
      + (desc ? '<p class="ds-modal__desc">' + desc + "</p>" : "")
      + (body ? '<div class="ds-modal__body">' + body + "</div>" : "")
      + '<div class="ds-modal__actions">' + actions + "</div></div>";
  };
  EE.openModal = function (html) {
    EE.closeModal();
    var wrap = document.createElement("div");
    wrap.className = "modalwrap";
    wrap.id = "modal";
    wrap.innerHTML = '<div class="ds-modal-scrim" data-act="modal-close"></div>' + html;
    document.body.appendChild(wrap);
    var first = wrap.querySelector("textarea, input, .ds-btn--secondary");
    if (first) first.focus();
  };
  EE.closeModal = function () { var m = EE.$("#modal"); if (m) m.remove(); };

  // The keyboard key in a corner of a button (.kbd in the proposal screens).
  EE.kbd = function (key) {
    var names = { escape: "Esc", space: "Space", tab: "Tab" };
    return '<span class="kbd">' + esc(names[key] || String(key).toUpperCase()) + "</span>";
  };

  // Strength dots on the ladder tiles: `level` of 5 lit.
  EE.dots = function (lit, total) {
    var out = "";
    for (var i = 0; i < (total || 5); i++) out += '<span class="sdot5' + (i < lit ? " on" : "") + '"></span>';
    return '<span class="sdots" aria-hidden="true">' + out + "</span>";
  };

  // Certainty dots (A3): 0-3 lit.
  EE.sure = function (n) {
    var k = n >= 5 ? 3 : n >= 3 ? 2 : n >= 1 ? 1 : 0;
    var words = ["not tried yet", "still guessing", "fairly sure", "quite sure"][k];
    var dots = [0, 1, 2].map(function (i) { return '<span class="cdot' + (i < k ? " on" : "") + '"></span>'; }).join("");
    return { k: k, words: words, html: '<span class="cdots" aria-hidden="true">' + dots + "</span>" };
  };
})();
