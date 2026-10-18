/* The /v5 launcher page (try/launcher-ui): Home, Set up and Waking up.
   It only talks to the launcher's JSON API (eepy/launcher/__main__.py, UI_GUIDE.md section 5):
   GET /api/status every second, POST /api/settings and /api/start-all, GET /api/logs and
   /api/rewards/sessions. The old page at / is not touched.
   Screens are hash routes: #/ (Home), #/setup, #/starting. */
(function () {
  "use strict";

  var $ = function (sel, el) { return (el || document).querySelector(sel); };
  var nav = $("#nav"), main = $("#view");

  function esc(text) {
    return String(text == null ? "" : text).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  // localStorage can be missing or blocked (private window): never let that break the page.
  var store = {
    get: function (key) { try { return window.localStorage.getItem(key); } catch (e) { return null; } },
    set: function (key, value) {
      try { if (value == null) window.localStorage.removeItem(key); else window.localStorage.setItem(key, value); } catch (e) { /* ignore */ }
    }
  };

  // The panel's default participant_pattern (eepy/settings.py): a study code, never a name.
  var PARTICIPANT = /^P[0-9]{2,3}$/;
  var BACKENDS = [
    { value: "dry", label: "No robot", description: "I stay still. Good for a dry run." },
    { value: "sim", label: "Simulator", description: "A virtual me, in my own window." },
    { value: "real", label: "Real robot", description: "The real me. I'll ask for my address." }
  ];
  var MODES = [
    { value: "wizard", label: "You", description: "I only do what you press." },
    { value: "rules", label: "Simple rules", description: "I follow fixed if-then rules." },
    { value: "hmm", label: "I estimate", description: "I read how they're doing and climb a fixed ladder." },
    { value: "hmm_bandit", label: "I estimate and learn", description: "…and I learn what works for each person." }
  ];
  var PART_NAMES = { sim: "My body", panel: "My control panel", vision: "My eyes" };
  var INSTALL = {
    robot: "python3.12 -m venv .venv-robot\n.venv-robot/bin/pip install -r requirements-robot.txt",
    vision: "python3.12 -m venv .venv-vision\n.venv-vision/bin/pip install -r requirements-vision.txt"
  };

  var status = null;          // the last /api/status
  var view = "";              // the screen on show
  var draft = null;           // the Set up choices, before they are saved
  var known = [];             // participant codes from the recorded sessions
  var helpOpen = false;
  var startError = "";
  var logTail = { part: "", lines: [] };

  function labelOf(list, value) {
    for (var i = 0; i < list.length; i++) if (list[i].value === value) return list[i].label;
    return value || "";
  }

  // --- the API ---------------------------------------------------------------------------------

  function getJSON(path) {
    return fetch(path, { cache: "no-store" }).then(function (r) { return r.json(); });
  }
  function post(path, body) {
    return fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) })
      .then(function (r) { return r.json(); })
      .catch(function () { return { ok: false, message: "The launcher did not answer." }; });
  }

  function parts() { return (status && status.parts) || {}; }
  function needed() { return (status && status.needed) || []; }
  function isUp(state) { return state === "running" || state === "external"; }
  function missing(name) { var p = parts()[name]; return !!(p && p.missing && p.missing.length); }

  // --- day / night: Auto follows the computer (prefers-color-scheme in tokens.css) ------------

  function appearance() {
    var v = store.get("eepy-appearance");
    return v === "light" || v === "dark" ? v : "auto";
  }
  function applyTheme() {
    var v = appearance();
    if (v === "auto") document.documentElement.removeAttribute("data-theme");
    else document.documentElement.setAttribute("data-theme", v);
  }
  function setAppearance(v) {
    store.set("eepy-appearance", v === "auto" ? null : v);
    applyTheme();
    var box = $("#appear");
    if (box) box.outerHTML = appearanceSwitch();
  }
  window.addEventListener("storage", function (e) {
    if (e.key === "eepy-appearance") { applyTheme(); var box = $("#appear"); if (box) box.outerHTML = appearanceSwitch(); }
  });

  function appearanceSwitch() {
    var cur = appearance();
    var opt = function (v, ic, text, title) {
      return '<button type="button" role="radio" class="appear__opt' + (cur === v ? " is-on" : "") + '" aria-checked="' + (cur === v)
        + '" data-act="appearance" data-value="' + v + '"' + (title ? ' title="' + title + '"' : "") + ">" + ic + text + "</button>";
    };
    var monitor = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><rect x="3" y="4" width="18" height="12" rx="2"></rect><path d="M8 20h8M12 16v4"></path></svg>';
    var sun = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="12" r="4"></circle><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"></path></svg>';
    var moon = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"></path></svg>';
    return '<div class="appear" id="appear" role="radiogroup" aria-label="Appearance">'
      + '<span class="caption muted" style="text-align: right;">Appearance<br><span class="micro">' + (cur === "auto" ? "follows your computer" : "saved for every screen") + "</span></span>"
      + '<div class="appear__seg">' + opt("auto", monitor, "Auto", "Follow my computer's day/night setting") + opt("light", sun, "Light") + opt("dark", moon, "Dark") + "</div></div>";
  }

  // --- the top bar -------------------------------------------------------------------------------

  function renderNav() {
    var link = function (href, text, on) {
      return '<a class="ds-nav__link" href="' + href + '"' + (on ? ' aria-current="page"' : "") + ">" + text + "</a>";
    };
    nav.innerHTML = '<div class="ds-nav__bar">'
      + '<div class="ds-nav__brand"><a href="#/" aria-label="EEPY, back to Home"><img src="img/eepy-mark.svg" alt="">EEPY</a></div>'
      + '<nav class="ds-nav__links" aria-label="Main">' + link("#/", "Home", view === "home")
      + link("#/setup", "Run a session", view === "setup" || view === "starting") + "</nav>"
      + '<a class="nav-old" href="/">Current launcher</a></div>';
  }

  // --- Home --------------------------------------------------------------------------------------

  function lastRun() {
    try { var v = JSON.parse(store.get("eepy-last") || "null"); return v && PARTICIPANT.test(v.participant) ? v : null; } catch (e) { return null; }
  }

  function renderHome() {
    var now = new Date(), hour = now.getHours();
    var part = hour < 12 ? "morning" : hour < 18 ? "afternoon" : "evening";
    var greet = { morning: "Good morning!", afternoon: "Good afternoon!", evening: "Good evening!" }[part];
    var ic = { morning: "Coffee", afternoon: "BookOpen", evening: "BedDouble" }[part];
    var clock = String(hour).padStart(2, "0") + ":" + String(now.getMinutes()).padStart(2, "0");
    var last = lastRun();
    main.innerHTML = '<section class="home">'
      + '<div class="hero"><span class="hero__glow glow" aria-hidden="true"></span><span class="hero__shadow" aria-hidden="true"></span>'
      + '<img class="bob" src="img/eepy-wave.png" alt="EEPY waving hello"></div>'
      + '<div style="display: flex; flex-direction: column; gap: var(--space-xl);">'
      + '<section class="hello rise" aria-label="EEPY says hello">'
      + '<div style="display: flex; align-items: center; gap: var(--space-xs); margin-bottom: var(--space-md);">'
      + '<span class="caption timechip">' + icon(ic, 16) + part.charAt(0).toUpperCase() + part.slice(1) + " · " + clock + "</span>"
      + '<span class="caption muted">EEPY, your study buddy</span></div>'
      + '<h1 class="display-lg">' + greet + "</h1>"
      + '<p class="body-lg muted" style="margin: var(--space-sm) 0 var(--space-lg);">Shall we study together? Here\'s how I\'ll help:</p>'
      + '<div class="helps">'
      + '<div><span class="ic" style="color: var(--time-focus-start);">' + icon("BookOpen", 16) + '</span><span class="body-sm">I keep you company while you work</span></div>'
      + '<div><span class="ic" style="color: var(--time-focus-start);">' + icon("Search", 16) + '</span><span class="body-sm">I notice when your focus slips</span></div>'
      + '<div><span class="ic" style="color: var(--time-break);">' + icon("Coffee", 16) + '</span><span class="body-sm">I suggest a break at the right time</span></div>'
      + "</div></section>"
      + '<div class="actions rise">'
      + '<a href="#/setup" class="ds-btn ds-btn--primary btn-big"><span class="ds-btn__label">Start a session</span>' + icon("ArrowRight", 16) + "</a>"
      + '<button type="button" class="ds-btn ds-btn--secondary btn-big" data-act="same" style="border: 1px solid var(--hairline-strong);"'
      + (last ? ' title="' + esc(last.participant + " · " + labelOf(BACKENDS, last.backend) + " · " + labelOf(MODES, last.mode)) + '"' : ' disabled title="Nothing to repeat yet: start a session first."')
      + ">" + icon("Play", 16) + '<span class="ds-btn__label">Same as last time</span></button>'
      + "</div></div></section>"
      + '<footer class="homefoot"><span id="camera-note"></span><div style="flex: 1;"></div>' + appearanceSwitch() + "</footer>";
    updateHome();
  }

  function updateHome() {
    var note = $("#camera-note");
    if (!note || !status) return;
    note.innerHTML = missing("vision")
      ? '<span class="ds-badge ds-badge--error"><span class="ds-badge__dot" aria-hidden="true"></span>My camera isn\'t set up</span> <span class="body-sm muted">No worries, I\'ll show you the fix in set up.</span>'
      : "";
  }

  // --- Set up --------------------------------------------------------------------------------------

  function radios(name, legend, options, value) {
    return '<fieldset class="ds-radio-group"><legend class="ds-field__label">' + legend + "</legend>"
      + options.map(function (o) {
        var id = name + "-" + o.value;
        return '<div class="ds-check ds-radio"><input id="' + id + '" type="radio" name="' + name + '" value="' + o.value + '" class="ds-check__input"'
          + (o.value === value ? " checked" : "") + '><span class="ds-check__box" aria-hidden="true"><span class="ds-radio__dot"></span></span>'
          + '<label for="' + id + '" class="ds-check__label">' + o.label + '<span class="ds-check__desc">' + o.description + "</span></label></div>";
      }).join("") + "</fieldset>";
  }

  function renderSetup() {
    if (!status) { main.innerHTML = '<p class="body muted" style="padding: 80px;">One moment…</p>'; return; }
    if (!draft) {
      var last = lastRun();
      draft = { participant: last ? last.participant : "", backend: status.settings.backend, mode: status.settings.mode,
                robot_host: status.settings.robot_host || "" };
    }
    startError = "";
    main.innerHTML = '<header class="pagehead"><div class="pagehead__text">'
      + '<span class="caption eyebrow">Run a session · step 1 of 3</span>'
      + '<h1 class="display-md">Let\'s get me ready</h1>'
      + '<span class="body-lg muted">Tell me who\'s studying and how we\'ll work. I\'ll check myself meanwhile.</span></div>'
      + '<ol class="steps" aria-label="Steps"><li aria-current="step">1 · Set up</li><li>2 · Session</li><li>3 · Wrap up</li></ol></header>'
      + '<section class="setup">'
      + '<section class="card"><div class="cardhead"><span class="ic">' + icon("GraduationCap", 20) + '</span><span class="headline">Who\'s studying?</span></div>'
      + '<div class="ds-field" id="code-field"><label class="ds-field__label" for="code">Participant code</label>'
      + '<div class="ds-field__control"><input id="code" class="ds-input" type="text" autocomplete="off" spellcheck="false" placeholder="e.g. P05" value="' + esc(draft.participant) + '" aria-describedby="code-msg"></div>'
      + '<p id="code-msg" class="ds-field__msg">I\'ll know them by this code only, never by their name.</p></div>'
      + '<div id="known" style="display: flex; flex-wrap: wrap; gap: var(--space-xs); align-items: center;"></div></section>'
      + '<section class="card">' + radios("backend", "Which me are we using?", BACKENDS, draft.backend)
      + '<div class="ds-field" id="host-field"' + (draft.backend === "real" ? "" : " hidden") + '><label class="ds-field__label" for="host">My address</label>'
      + '<div class="ds-field__control"><input id="host" class="ds-input" type="text" spellcheck="false" placeholder="' + esc(status.robot_host || "192.168.50.95") + '" value="' + esc(draft.robot_host) + '"></div>'
      + '<p class="ds-field__msg">Same Wi-Fi as me, please. Empty: ' + esc(status.robot_host || "the lab robot") + ".</p></div></section>"
      + '<section class="card">' + radios("mode", "Who decides when I step in?", MODES, draft.mode) + "</section>"
      + '<section class="card selfcheck" id="selfcheck"></section>'
      + "</section>"
      + '<footer class="stepbar"><a href="#/" class="ds-btn ds-btn--secondary"><span class="ds-btn__label">Back</span></a>'
      + '<div style="flex: 1;"></div><span class="body-sm muted stepbar__note" id="summary"></span>'
      + '<button type="button" class="ds-btn ds-btn--primary" data-act="start" id="start" style="box-shadow: var(--glow-focus);"><span class="ds-btn__label">Start session</span>' + icon("ArrowRight", 18) + "</button></footer>";
    renderKnown();
    updateSetup();
    getJSON("/api/rewards/sessions").then(function (data) {
      var seen = {};
      known = [];
      (data.sessions || []).forEach(function (s) {
        var m = /_(P[0-9]{2,3})_/.exec(s.name || "");
        if (m && !seen[m[1]]) { seen[m[1]] = true; known.push(m[1]); }
      });
      renderKnown();
    }).catch(function () { /* no sessions yet */ });
  }

  function renderKnown() {
    var box = $("#known");
    if (!box) return;
    if (!known.length) { box.innerHTML = ""; return; }
    box.innerHTML = '<span class="caption muted">Or pick someone I\'ve met:</span>' + known.slice(0, 8).map(function (code) {
      var on = code === draft.participant;
      return '<button type="button" class="chip' + (on ? " is-on" : "") + '" aria-pressed="' + on + '" data-act="pick" data-value="' + code + '">' + code + "</button>";
    }).join("");
  }

  function selfChecks() {
    var rows = [], help = [];
    var robotMissing = missing("panel") || (draft.backend === "sim" && missing("sim"));
    rows.push(robotMissing ? ["bad", "My robot software is missing"] : ["ok", "My robot software"]);
    if (robotMissing) help.push(INSTALL.robot);
    rows.push(status.other_panel ? ["bad", "Another control panel is already open"] : ["ok", "No other control panel open"]);
    if (missing("vision")) { rows.push(["bad", "My camera software is missing"]); help.push(INSTALL.vision); }
    else rows.push(["ok", "My camera software"]);
    if (draft.backend === "real" && status.robot) {
      rows.push(status.robot.reachable ? ["ok", "I can reach my body at " + status.robot.host] : ["bad", "I can't reach my body at " + status.robot.host]);
    }
    return { rows: rows, help: help };
  }

  function updateSetup() {
    var box = $("#selfcheck");
    if (!box || !status || !draft) return;
    var c = selfChecks();
    var advice = [];
    if (missing("vision")) advice.push("Without my camera software I can't see how they're doing. We can still do a session where you call the shots.");
    if (status.other_panel) advice.push("Close the other panel first (Ctrl+C in its terminal), or I would record into its session.");
    if (draft.backend === "real" && status.robot && !status.robot.reachable) advice.push("Check that I'm switched on and on the same Wi-Fi.");
    box.innerHTML = '<div style="display: flex; flex-direction: column; gap: var(--space-sm);"><span class="headline">I checked myself</span>'
      + c.rows.map(function (r) {
        return '<span class="body chk"><span class="' + r[0] + '">' + icon(r[0] === "ok" ? "Check" : "CircleAlert", 18) + "</span>" + esc(r[1]) + "</span>";
      }).join("") + "</div>"
      + '<img src="img/eepy-builder.svg" alt="">'
      + '<div style="grid-column: 1 / span 2; display: flex; flex-direction: column; gap: var(--space-sm);">'
      + advice.map(function (a) { return '<span class="body-sm muted">' + esc(a) + "</span>"; }).join("")
      + (c.help.length
        ? (helpOpen
          ? '<div class="code">' + esc(c.help.join("\n")) + '</div><span class="caption muted">Run these in the EEPY folder (README, step 1).</span>'
            + '<div style="display: flex; gap: var(--space-xs);"><button type="button" class="ds-btn ds-btn--secondary" data-act="copy" style="background: var(--surface-2);"><span class="ds-btn__label">Copy commands</span></button>'
            + '<button type="button" class="ds-btn ds-btn--secondary" data-act="recheck" style="background: var(--surface-2);"><span class="ds-btn__label">Check again</span></button></div>'
          : '<button type="button" class="ds-btn ds-btn--secondary" data-act="help" style="background: var(--surface-2); align-self: flex-start;"><span class="ds-btn__label">How do I install it?</span></button>')
        : "")
      + "</div>";
    var summary = $("#summary");
    if (summary) {
      summary.innerHTML = esc((draft.participant || "No code yet") + " · " + labelOf(BACKENDS, draft.backend) + " · " + labelOf(MODES, draft.mode))
        + '<br><span class="caption">' + (startError ? '<span style="color: var(--error);">' + esc(startError) + "</span>" : "Then I wake up my body, my control panel and my eyes.") + "</span>";
    }
    var start = $("#start");
    if (start) {
      start.disabled = !!status.busy;
      start.title = status.busy ? "I'm still busy starting or stopping." : "";
    }
  }

  function showCodeError(text) {
    var field = $("#code-field"), msg = $("#code-msg");
    if (!field || !msg) return;
    field.classList.toggle("is-error", !!text);
    msg.className = "ds-field__msg" + (text ? " ds-field__msg--error" : "");
    msg.innerHTML = text ? icon("CircleAlert", 14) + "<span>" + esc(text) + "</span>" : "I'll know them by this code only, never by their name.";
    $("#code").setAttribute("aria-invalid", text ? "true" : "false");
  }

  function startSession() {
    var code = (draft.participant || "").trim().toUpperCase();
    draft.participant = code;
    if (!PARTICIPANT.test(code)) {
      showCodeError(code ? '"' + code + '" isn\'t a participant code. Use the study\'s code, like P05: never a name.' : "Type the participant code first, like P05.");
      $("#code").focus();
      return;
    }
    showCodeError("");
    var settings = { backend: draft.backend, mode: draft.mode, robot_host: draft.robot_host.trim() };
    var s = status.settings;
    var changed = settings.backend !== s.backend || settings.mode !== s.mode || settings.robot_host !== (s.robot_host || "");
    var anyOurs = Object.keys(parts()).some(function (n) { return parts()[n].pid; });
    var button = $("#start");
    if (button) button.disabled = true;
    post("/api/settings", settings).then(function (res) {
      if (!res.ok) throw new Error(res.message || "Those settings were not accepted.");
      // Programs I started with the old settings would keep them: restart them with the new ones.
      return changed && anyOurs ? post("/api/stop-all", {}) : null;
    }).then(function () {
      return post("/api/start-all", {});
    }).then(function () {
      store.set("eepy-last", JSON.stringify({ participant: code, backend: settings.backend, mode: settings.mode }));
      location.hash = "#/starting";
    }).catch(function (err) {
      startError = err.message;
      if (button) button.disabled = false;
      updateSetup();
    });
  }

  // --- Waking up -----------------------------------------------------------------------------------

  var FAIL = /stopped during start-up|did not come up|not started|does not exist|could not start|^Stopped:|already in use/i;

  function wakeState() {
    var list = needed().map(function (n) { return { name: n, state: (parts()[n] || {}).state || "stopped" }; });
    var done = list.filter(function (p) { return isUp(p.state); }).length;
    var failed = !status.busy && done < list.length && FAIL.test(status.message || "");
    var bad = failed ? (list.filter(function (p) { return !isUp(p.state); })[0] || {}).name : "";
    return { list: list, done: done, all: list.length > 0 && done === list.length, failed: failed, bad: bad,
             idle: !status.busy && !failed && done < list.length };
  }

  function renderStarting() {
    if (!status) { main.innerHTML = '<p class="body muted" style="padding: 80px;">One moment…</p>'; return; }
    var w = wakeState();
    var pills = '<ol class="parts">' + w.list.map(function (p) {
      var label = (parts()[p.name] || {}).label || "";
      var up = isUp(p.state), now = !up && (p.state === "starting" || (status.busy && !!label && (status.step || "").indexOf(label) >= 0));
      var isBad = w.failed && p.name === w.bad;
      var mark = up ? '<span class="ok">' + icon("Check", 16) + "</span>" : isBad ? '<span class="bad">' + icon("X", 16) + "</span>"
        : now ? '<span class="spin-dot" aria-hidden="true"></span>' : '<span class="idle-dot" aria-hidden="true"></span>';
      return '<li class="' + (now ? "is-now" : "") + (isBad ? " is-bad" : "") + '"' + (up || now || isBad ? "" : ' style="color: var(--ink-muted);"') + ">" + mark + PART_NAMES[p.name] + "</li>";
    }).join("") + "</ol>";
    var frac = w.list.length ? w.done / w.list.length : 0;
    var ring = '<div class="ring"><svg width="260" height="260" viewBox="0 0 260 260" aria-hidden="true"><circle cx="130" cy="130" r="120" fill="none" style="stroke: var(--time-track);" stroke-width="6"></circle>'
      + '<circle cx="130" cy="130" r="120" fill="none" style="stroke: ' + (w.failed ? "var(--error)" : "var(--time-focus)") + ';" stroke-width="6" stroke-linecap="round" stroke-dasharray="' + Math.max(8, frac * 754).toFixed(0) + ' 754" transform="rotate(-90 130 130)"></circle></svg>'
      + (status.busy ? '<svg class="spin" width="260" height="260" viewBox="0 0 260 260" aria-hidden="true"><circle cx="130" cy="130" r="120" fill="none" style="stroke: var(--time-focus-start);" stroke-width="6" stroke-linecap="round" stroke-dasharray="60 694"></circle></svg>' : "")
      + '<img class="' + (w.failed ? "" : "bob") + '" src="img/eepy-builder.svg" alt="EEPY in a builder\'s hat"></div>';
    var title, text, extra = "", buttons;
    if (w.failed) {
      title = { vision: "I couldn't open my eyes.", panel: "My control panel didn't start.", sim: "My body didn't start." }[w.bad] || "Something didn't start.";
      text = "So I stopped there and didn't start the rest.";
      if (logTail.part !== w.bad) { logTail = { part: w.bad, lines: [] }; fetchLog(w.bad); }
      extra = '<section class="wakebox"><div style="display: flex; flex-direction: column; gap: var(--space-xs);">'
        + '<span class="caption eyebrow">What happened</span><span class="body-sm">' + esc(status.message) + "</span>"
        + (missing(w.bad) ? '<span class="body-sm muted">Its software is missing: set it up first (README, step 1).</span>' : "")
        + '</div><div style="display: flex; flex-direction: column; gap: var(--space-xs);"><span class="caption eyebrow">What its log says</span>'
        + '<span class="code">' + esc(logTail.lines.join("\n") || "…") + "</span>"
        + '<a href="/" class="caption">Open the full log in the current launcher</a></div></section>';
      buttons = '<div class="actions"><a href="#/setup" class="ds-btn ds-btn--secondary"><span class="ds-btn__label">Back to set up</span></a>'
        + '<button type="button" class="ds-btn ds-btn--primary" data-act="retry"><span class="ds-btn__label">Try again</span></button></div>';
    } else if (w.all) {
      var panel = parts().panel || {};
      title = "I'm awake!";
      text = "My " + w.list.map(function (p) { return PART_NAMES[p.name].replace("My ", ""); }).join(", ") + " are ready.";
      buttons = '<div class="actions"><a href="#/" class="ds-btn ds-btn--secondary"><span class="ds-btn__label">Back to Home</span></a>'
        + (panel.url ? '<a href="' + esc(panel.url) + '" target="_blank" rel="noopener" class="ds-btn ds-btn--primary"><span class="ds-btn__label">Open my control panel</span>' + icon("ArrowRight", 16) + "</a>" : "")
        + '</div><span class="caption muted">The new control panel screen comes next: for now it opens the current Wizard panel.</span>';
    } else if (w.idle) {
      title = "Nothing is starting right now.";
      text = "Set up a session first, and I'll wake up.";
      buttons = '<div class="actions"><a href="#/setup" class="ds-btn ds-btn--primary"><span class="ds-btn__label">Go to set up</span></a></div>';
    } else {
      title = "Waking up…";
      text = esc(status.step || "I'm starting everything myself.");
      buttons = '<span class="caption muted">If something won\'t start, I\'ll tell you right here what to do.</span>';
    }
    main.innerHTML = '<section class="wake">' + ring
      + '<div style="display: flex; flex-direction: column; align-items: center; gap: var(--space-xs);">'
      + '<span class="caption eyebrow"' + (w.failed ? ' style="color: var(--error);"' : "") + ">Run a session · step 2 of 3 · waking up</span>"
      + '<h1 class="display-md" aria-live="polite">' + title + '</h1><span class="body-lg muted" style="max-width: 560px;">' + text + "</span></div>"
      + pills + extra + buttons + "</section>";
  }

  function fetchLog(part) {
    getJSON("/api/logs?since=0&part=" + encodeURIComponent(part)).then(function (data) {
      if (logTail.part !== part) return;
      logTail.lines = (data.lines || []).slice(-6).map(function (row) { return row[2]; });
      if (view === "starting") renderStarting();
    }).catch(function () { /* keep the dots */ });
  }

  // --- wiring ------------------------------------------------------------------------------------

  main.addEventListener("click", function (e) {
    var el = e.target.closest("[data-act]");
    if (!el || el.disabled) return;
    var act = el.getAttribute("data-act");
    if (act === "appearance") setAppearance(el.getAttribute("data-value"));
    else if (act === "same") {
      post("/api/start-all", {}).then(function () { location.hash = "#/starting"; });
    } else if (act === "pick") {
      draft.participant = el.getAttribute("data-value");
      $("#code").value = draft.participant;
      showCodeError("");
      renderKnown();
      updateSetup();
    } else if (act === "help") { helpOpen = true; updateSetup(); }
    else if (act === "copy") {
      var text = selfChecks().help.join("\n");
      if (navigator.clipboard) navigator.clipboard.writeText(text).then(function () { el.querySelector(".ds-btn__label").textContent = "Copied"; });
    } else if (act === "recheck") refresh();
    else if (act === "start") startSession();
    else if (act === "retry") post("/api/start-all", {}).then(refresh);
  });

  main.addEventListener("input", function (e) {
    if (!draft) return;
    if (e.target.id === "code") { draft.participant = e.target.value.trim().toUpperCase(); showCodeError(""); renderKnown(); updateSetup(); }
    else if (e.target.id === "host") { draft.robot_host = e.target.value; }
  });
  main.addEventListener("change", function (e) {
    if (!draft || e.target.type !== "radio") return;
    draft[e.target.name] = e.target.value;
    if (e.target.name === "backend") $("#host-field").hidden = draft.backend !== "real";
    updateSetup();
  });

  function route() {
    var h = location.hash.replace(/^#\/?/, "");
    return h === "setup" ? "setup" : h === "starting" ? "starting" : "home";
  }

  function show() {
    var next = route();
    if (next !== "setup") draft = null;
    view = next;
    renderNav();
    if (view === "home") renderHome();
    else if (view === "setup") renderSetup();
    else renderStarting();
    main.focus({ preventScroll: true });
  }

  function refresh() {
    return getJSON("/api/status").then(function (data) {
      var first = !status;
      status = data;
      if (first && view === "setup") renderSetup();
      else if (view === "home") updateHome();
      else if (view === "setup") updateSetup();
      else if (view === "starting") renderStarting();
    }).catch(function () { /* the launcher is restarting: try again next second */ });
  }

  applyTheme();
  window.addEventListener("hashchange", show);
  show();
  refresh();
  setInterval(refresh, 1000);
})();
