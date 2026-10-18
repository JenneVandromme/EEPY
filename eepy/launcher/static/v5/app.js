/* The /v5 launcher page (try/launcher-ui). It only talks to the launcher (eepy/launcher/__main__.py):
   its JSON API (GET /api/status every second, /api/settings, /api/start-all, /api/logs, /api/bandit/run)
   and the /v5 page's own calls in v5api.py: the control panel's API passed through
   (/api/v5/panel/...), the robot's volume, and the recorded sessions (read only).
   The old page at / is not touched.
   Screens are hash routes: #/ (Home), #/setup, #/starting, #/desk, #/control, #/done/<session>,
   #/sessions, #/story/<session>, #/learned, #/advanced. */
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
      + link("#/setup", "Run a session", ["setup", "starting", "desk", "control", "done"].indexOf(view) >= 0)
      + link("#/sessions", "Analyze", ["sessions", "story", "learned"].indexOf(view) >= 0)
      + link("#/advanced", "Advanced tools", view === "advanced") + "</nav>"
      + '<span id="nav-session"></span><a class="nav-old" href="/">Current launcher</a></div>';
    updateNav();
  }

  // A session is recording: a way back to it from every screen.
  function updateNav() {
    var sess = (ps() && ps().session) || {};
    var here = view === "control" || view === "desk";
    setHTML($("#nav-session"), sess.active && !here
      ? '<a class="nav-live" href="#/control"><span class="ds-badge__dot" aria-hidden="true"></span>' + esc(sess.participant) + " is recording · back to the session</a>" : "");
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
      + '<footer class="homefoot"><span id="camera-note"></span><span class="body-sm bad-text" id="home-msg"></span><div style="flex: 1;"></div>' + appearanceSwitch() + "</footer>";
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
    var button = $("#start");
    if (button) button.disabled = true;
    launch(code, { backend: draft.backend, mode: draft.mode, robot_host: draft.robot_host.trim() }).catch(function (err) {
      startError = err.message;
      if (button) button.disabled = false;
      updateSetup();
    });
  }

  // Save the settings, wake everything up, then (on Waking up) open the participant's session.
  function launch(code, settings) {
    var s = status.settings;
    var changed = settings.backend !== s.backend || settings.mode !== s.mode || settings.robot_host !== (s.robot_host || "");
    var anyOurs = Object.keys(parts()).some(function (n) { return parts()[n].pid; });
    var open = (ps() && ps().session) || {};
    if (changed && anyOurs && open.active) {
      return Promise.reject(new Error(open.participant + "'s session is still recording: end it before changing how I work."));
    }
    return post("/api/settings", settings).then(function (res) {
      if (!res.ok) throw new Error(res.message || "Those settings were not accepted.");
      // Programs I started with the old settings would keep them: restart them with the new ones.
      return changed && anyOurs ? post("/api/stop-all", {}) : null;
    }).then(function () {
      return post("/api/start-all", {});
    }).then(function () {
      store.set("eepy-last", JSON.stringify({ participant: code, backend: settings.backend, mode: settings.mode }));
      startIntent = true;
      ask = { busy: false, error: "", other: "" };
      location.hash = "#/starting";
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
      // Without my eyes a session still works (you call the shots, nothing is estimated): offer it.
      var noEyes = w.bad === "vision" && isUp((parts().panel || {}).state) && lastRun();
      if (ask.error) extra += '<p class="body-sm bad-text">' + icon("CircleAlert", 16) + esc(ask.error) + "</p>";
      buttons = '<div class="actions"><a href="#/setup" class="ds-btn ds-btn--secondary"><span class="ds-btn__label">Back to set up</span></a>'
        + (noEyes ? '<button type="button" class="ds-btn ds-btn--secondary" data-act="begin"><span class="ds-btn__label">Start without my eyes</span></button>' : "")
        + '<button type="button" class="ds-btn ds-btn--primary" data-act="retry"><span class="ds-btn__label">Try again</span></button></div>'
        + (noEyes ? '<span class="caption muted">Without my eyes I can\'t see how they\'re doing: you call the shots, and I record everything you do.</span>' : "");
    } else if (w.all) {
      var last = lastRun();
      if (startIntent && !ask.busy && !ask.error) beginSession();
      title = "I'm awake!";
      if (ask.error) {
        title = "I'm awake, but the session didn't open.";
        text = esc(ask.error);
        buttons = '<div class="actions"><a href="#/setup" class="ds-btn ds-btn--secondary"><span class="ds-btn__label">Back to set up</span></a>'
          + (ask.other
            ? '<a href="#/control" class="ds-btn ds-btn--secondary"><span class="ds-btn__label">Go to ' + esc(ask.other) + "'s session</span></a>"
              + (last ? '<button type="button" class="ds-btn ds-btn--primary" data-act="end-other"><span class="ds-btn__label">End it and start ' + esc(last.participant) + "'s</span></button>" : "")
            : '<button type="button" class="ds-btn ds-btn--primary" data-act="begin"><span class="ds-btn__label">Try again</span></button>')
          + "</div>";
      } else if (startIntent || ask.busy) {
        text = "Opening " + esc(last ? last.participant : "the") + "'s session…";
        buttons = '<span class="caption muted">Then I\'ll check the desk.</span>';
      } else {
        text = "My " + w.list.map(function (p) { return PART_NAMES[p.name].replace("My ", ""); }).join(", ") + " are ready.";
        buttons = '<div class="actions"><a href="#/" class="ds-btn ds-btn--secondary"><span class="ds-btn__label">Back to Home</span></a>'
          + (last ? '<button type="button" class="ds-btn ds-btn--primary" data-act="begin"><span class="ds-btn__label">Start ' + esc(last.participant) + "'s session</span>" + icon("ArrowRight", 16) + "</button>"
            : '<a href="#/setup" class="ds-btn ds-btn--primary"><span class="ds-btn__label">Set up a session</span></a>') + "</div>";
      }
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

  // --- Advanced tools ------------------------------------------------------------------------------

  var STATE_TEXT = { stopped: "Stopped", starting: "Starting…", running: "Running", stopping: "Stopping…",
                     crashed: "Crashed", external: "Running in a terminal" };
  var STATE_TONE = { running: "success", crashed: "error" };
  var CAMERAS = [
    { value: "", label: "Default (the robot's camera with the real robot, else config/eepy.yaml)" },
    { value: "auto", label: "Auto" }, { value: "robot", label: "The robot's camera" },
    { value: "laptop", label: "Laptop camera" }, { value: "fake", label: "No camera (fake)" }
  ];
  var LOG_PARTS = [{ value: "", label: "Everything" }, { value: "launcher", label: "Launcher" }, { value: "sim", label: "My body" },
                   { value: "panel", label: "My control panel" }, { value: "vision", label: "My eyes" }];
  var advDirty = false, advMsg = "", pending = {};
  var logs = [], lastLog = 0, logFilter = "", follow = true;

  function field(id, label, value, hint, type) {
    return '<div class="ds-field"><label class="ds-field__label" for="' + id + '">' + label + '</label><div class="ds-field__control">'
      + '<input id="' + id + '" class="ds-input" type="' + (type || "text") + '" spellcheck="false" value="' + esc(value) + '"></div>'
      + (hint ? '<p class="ds-field__msg">' + hint + "</p>" : "") + "</div>";
  }
  function select(id, label, options, value) {
    return '<div class="ds-field"><label class="ds-field__label" for="' + id + '">' + label + '</label><div class="ds-field__control ds-select">'
      + '<select id="' + id + '" class="ds-input ds-select__el">' + options.map(function (o) {
        return '<option value="' + o.value + '"' + (o.value === value ? " selected" : "") + ">" + o.label + "</option>";
      }).join("") + "</select>" + icon("ChevronRight", 16).replace('class="ds-icon"', 'class="ds-icon ds-select__chev"') + "</div></div>";
  }
  function section(ic, title, hint) {
    return '<div class="cardhead"><span class="ic">' + icon(ic, 18) + '</span><span style="display: flex; flex-direction: column;"><span class="headline">'
      + title + '</span><span class="caption muted">' + hint + "</span></span></div>";
  }

  function renderAdvanced() {
    if (!status) { main.innerHTML = '<p class="body muted" style="padding: 80px;">One moment…</p>'; return; }
    var s = status.settings;
    advDirty = false;
    main.innerHTML = '<header class="pagehead"><img src="img/eepy-builder.svg" alt="" style="width: 84px; height: 84px; object-fit: contain;">'
      + '<div class="pagehead__text"><span class="caption eyebrow">Advanced tools · for developers</span><h1 class="display-md">Under my hood</h1>'
      + '<span class="body-lg muted">See what\'s running, change my settings, fix me when I\'m stuck. All of it also works from the terminal.</span></div>'
      + '<span id="classic"></span></header>'
      + '<section class="adv">'
      + '<div class="adv__programs"><div class="cardhead"><span class="ic">' + icon("Play", 18) + '</span><span class="headline">My programs</span></div>'
      + '<div class="adv__row" id="programs"></div></div>'
      + '<div class="adv__row adv__row--2">'
      + '<section class="card">' + section("Menu", "Settings", "Where to find my camera and robot. Saved for this computer.")
      + '<form id="settings" class="adv__form" autocomplete="off">'
      + select("appearance", "Appearance", [{ value: "auto", label: "Follow my computer (day/night)" }, { value: "light", label: "Always light" }, { value: "dark", label: "Always dark" }], appearance())
      + select("camera", "Camera", CAMERAS, s.camera)
      + field("robot_host", "Real robot address", s.robot_host, "Empty: " + esc(status.robot_host) + ".")
      + '<div class="adv__pair">' + field("panel_port", "Panel port", s.panel_port, "", "number") + field("live_port", "Live page port", s.live_port, "", "number") + "</div>"
      + field("sim_extra", "Extra arguments · my body", s.sim_extra, "e.g. --headless")
      + field("panel_extra", "Extra arguments · my control panel", s.panel_extra, "e.g. --session-prior prior.json")
      + field("vision_extra", "Extra arguments · my eyes", s.vision_extra, "e.g. --no-aus")
      + '<div style="display: flex; align-items: center; gap: var(--space-sm); flex-wrap: wrap;"><button type="submit" class="ds-btn ds-btn--primary"><span class="ds-btn__label">Save settings</span></button>'
      + '<span class="caption muted" id="save-msg">I\'ll tell you what to restart.</span></div></form></section>'
      + '<section class="card">' + section("Zap", "Everything at once", "In order: body, control panel, eyes. Stop goes the other way.")
      + '<div style="display: flex; gap: var(--space-xs); flex-wrap: wrap;"><button type="button" class="ds-btn ds-btn--secondary" data-act="start-all" style="background: var(--surface-2);"><span class="ds-btn__label">Start everything</span></button>'
      + '<button type="button" class="ds-btn ds-btn--secondary" data-act="stop-all" style="background: var(--surface-2);"><span class="ds-btn__label">Stop everything</span></button></div>'
      + '<p class="body-sm" id="adv-message" style="margin: 0;"></p>'
      + '<span class="caption muted">Stopping my control panel ends the session and puts me back to neutral.</span>'
      + '<div style="margin-top: var(--space-md); padding-top: var(--space-md); border-top: 1px solid var(--hairline-soft); display: flex; flex-direction: column; gap: var(--space-xs);">' + section("ChevronRight", "Same thing, in terminals", "One line per program, in the EEPY folder.")
      + '<div class="code" id="commands"></div><button type="button" class="ds-btn ds-btn--secondary" data-act="copy-commands" style="background: var(--surface-2); align-self: flex-start;"><span class="ds-btn__label">Copy all</span></button></div></section>'
      + "</div>"
      + '<section class="card">'
      + '<div style="display: flex; align-items: flex-end; gap: var(--space-md); flex-wrap: wrap;">' + '<div style="flex: 1; min-width: 240px;">' + section("Search", "Everything I printed", "Live messages from the launcher and my three programs.") + "</div>"
      + '<div style="width: 200px;">' + select("log-filter", "Show", LOG_PARTS, logFilter) + "</div>"
      + '<div class="ds-check"><input id="follow" type="checkbox" class="ds-check__input"' + (follow ? " checked" : "") + '><span class="ds-check__box" aria-hidden="true">' + icon("Check", 14) + '</span><label for="follow" class="ds-check__label">Follow</label></div>'
      + '<button type="button" class="ds-btn ds-btn--secondary" data-act="clear-log" style="background: var(--surface-2);"><span class="ds-btn__label">Clear view</span></button></div>'
      + '<div class="logbox" id="log" role="log" aria-live="off"></div></section>'
      + "</section>";
    updateAdvanced();
    renderLog();
  }

  function updateAdvanced() {
    if (!status || !$("#programs")) return;
    var s = status.settings, inUse = needed();
    $("#programs").innerHTML = ["sim", "panel", "vision"].map(function (name) {
      var p = parts()[name] || {}, st = p.state || "stopped", busy = pending[name];
      var used = inUse.indexOf(name) >= 0;
      var tone = STATE_TONE[st] || "neutral";
      var badge = '<span class="ds-badge ds-badge--' + tone + '">' + (tone !== "neutral" ? '<span class="ds-badge__dot" aria-hidden="true"></span>' : "") + STATE_TEXT[st] + "</span>";
      var btn = function (act, text) {
        return '<button type="button" class="ds-btn ds-btn--secondary" data-act="' + act + '" data-part="' + name + '" style="background: var(--surface-2);"' + (busy ? " disabled" : "") + '><span class="ds-btn__label">' + text + "</span></button>";
      };
      var actions;
      if (st === "external") actions = '<span class="caption muted">Started in a terminal: I leave it alone.</span>';
      else if (p.pid) actions = btn("part-restart", "Restart") + btn("part-stop", busy === "stop" ? "Stopping…" : "Stop");
      else if (p.missing && p.missing.length) actions = '<button type="button" class="ds-btn ds-btn--secondary" data-act="copy-install" data-part="' + name + '" style="background: var(--surface-2);"><span class="ds-btn__label">Copy install commands</span></button>';
      else actions = btn("part-start", busy === "start" ? "Starting…" : "Start");
      var facts = [];
      if (p.port) facts.push("port " + p.port);
      if (p.uptime != null) facts.push("up " + Math.max(1, Math.round(p.uptime / 60)) + " min");
      if (p.missing && p.missing.length) facts.push(esc(p.missing[0]) + " is missing");
      if (!used) facts.push("not used with " + labelOf(BACKENDS, s.backend).toLowerCase());
      return '<div class="card prog' + (st === "crashed" || (p.missing && p.missing.length) ? " is-bad" : "") + '">'
        + '<div style="display: flex; align-items: center; gap: var(--space-sm);"><span class="state-dot state-dot--' + st + '" aria-hidden="true"></span>'
        + '<b class="body" style="flex: 1;">' + PART_NAMES[name] + ' <span class="muted" style="font-weight: 400;">· ' + esc(p.label || name) + "</span></b>" + badge + "</div>"
        + '<span class="caption muted">' + facts.join(" · ") + "</span>"
        + '<div style="display: flex; gap: var(--space-xs); flex-wrap: wrap;">' + actions + "</div></div>";
    }).join("");
    $("#commands").textContent = inUse.map(function (n) { return (parts()[n] || {}).command || ""; }).join("\n\n");
    $("#adv-message").innerHTML = esc(advMsg || status.step || status.message || "");
    var panel = parts().panel || {};
    $("#classic").innerHTML = panel.url && isUp(panel.state)
      ? '<a href="' + esc(panel.url) + '" target="_blank" rel="noopener" class="body-sm" style="display: inline-flex; align-items: center; gap: var(--space-xxs);">Open the classic panel ' + icon("ArrowRight", 16) + "</a>" : "";
    if (!advDirty) {
      ["robot_host", "panel_port", "live_port", "sim_extra", "panel_extra", "vision_extra"].forEach(function (k) {
        var el = $("#" + k); if (el && document.activeElement !== el) el.value = s[k];
      });
      var cam = $("#camera"); if (cam && document.activeElement !== cam) cam.value = s.camera;
    }
  }

  function renderLog() {
    var box = $("#log");
    if (!box) return;
    var rows = logs.filter(function (r) { return !logFilter || r[1] === logFilter; }).slice(-800);
    box.textContent = rows.map(function (r) { return (r[1] + "        ").slice(0, 9) + r[2]; }).join("\n");
    if (follow) box.scrollTop = box.scrollHeight;
  }

  function pollLogs() {
    if (view !== "advanced") return;
    getJSON("/api/logs?since=" + lastLog).then(function (data) {
      if (data.lines && data.lines.length) {
        logs = logs.concat(data.lines).slice(-4000);
        renderLog();
      }
      lastLog = data.last || lastLog;
    }).catch(function () { /* next second */ });
  }

  function saveSettings() {
    var num = function (id) { return parseInt($("#" + id).value, 10); };
    var body = { camera: $("#camera").value, robot_host: $("#robot_host").value.trim(), panel_port: num("panel_port"), live_port: num("live_port"),
                 sim_extra: $("#sim_extra").value, panel_extra: $("#panel_extra").value, vision_extra: $("#vision_extra").value };
    post("/api/settings", body).then(function (res) {
      var msg = $("#save-msg");
      if (msg) { msg.textContent = res.message || ""; msg.style.color = res.ok ? "" : "var(--error)"; }
      if (res.ok) { advDirty = false; refresh(); }
    });
  }

  function partAction(act, name) {
    var path = { "part-start": "/api/start", "part-stop": "/api/stop", "part-restart": "/api/restart" }[act];
    pending[name] = act === "part-stop" ? "stop" : "start";
    updateAdvanced();
    post(path, { part: name }).then(function (res) {
      advMsg = res.message || "";
      delete pending[name];
      refresh();
      setTimeout(function () { advMsg = ""; }, 6000);
    });
  }

  // --- the session: the control panel's own API, through the launcher (/api/v5/panel/..., v5api.py) --

  var pstate = null;                      // the last /api/v5/panel/state: {status, panel, log, seq}
  var pError = "";                        // why it could not be read
  var startIntent = false;                // Start session / Same as last time: open the session once awake
  var ask = { busy: false, error: "", other: "" };
  var sessionName = "";                   // the session on show, so its summary can follow it
  var desk = { busy: false, msg: "", seen: "", reason: "Short of time", was: "" };
  var ctrl = { tone: "hedged", intensity: null, tab: "controls", logOpen: false, noteOpen: false, version: null, msg: "", msgBad: false };
  var buttons = {}, keys = {};            // the panel's buttons, by id and by key
  var volumeInfo = null;

  function ps() { return pstate && pstate.status; }
  function pollPanel() {
    return fetch("/api/v5/panel/state", { cache: "no-store" }).then(function (r) { return r.json(); }).then(function (data) {
      if (data && data.status) { pstate = data; pError = ""; }
      else { pstate = null; pError = (data && data.message) || "My control panel isn't answering."; }
    }).catch(function () { pstate = null; pError = "The launcher did not answer."; });
  }
  function panelPost(path, body) { return post("/api/v5/panel/" + path, body); }
  function setHTML(el, html) { if (el && el.__html !== html) { el.innerHTML = html; el.__html = html; } }

  var STATE_NAME = { focused: "Focused", tired: "A bit tired", distracted: "Drifting", drowsy: "Drowsy", very_drowsy: "Very drowsy",
                     absent: "Away from the desk", unknown: "Not sure yet" };
  var STATE_ORDER = ["focused", "tired", "distracted", "drowsy", "very_drowsy", "absent", "unknown"];
  var RUNG = {
    approving_nod: "Approving nod",
    refocus_study_buddy: "Just breathe", refocus_joint_attention: "Glance at the notes", refocus_curious: "Curious tilt",
    refocus_verbal: "Spoken check", refocus_break_offer: "Offer a break",
    bedtime_companion: "Quiet company", bedtime_subtle: "Tired look", bedtime_attentive: "Yawn at them",
    bedtime_verbal: "Spoken check", bedtime_negotiate: "Wind-down deal", bedtime_goodnight: "Time to rest"
  };
  var MARKER = { user_went_to_bed: "Stopped to rest", user_replied: "Replied", user_dismissed: "Dismissed it",
                 user_accepted: "Accepted it", user_snoozed: "Snoozed it" };
  var OUTCOME = { success: "Worked", no_reaction: "No reaction", dismissed: "Dismissed", snoozed: "Snoozed",
                  interrupted_focus: "They were already focused", censored: "No fair verdict" };
  var KEYNAME = { escape: "Esc", space: "Space", tab: "Tab" };

  function cap(text) { text = String(text || "").replace(/_/g, " "); return text.charAt(0).toUpperCase() + text.slice(1); }
  function behaviorName(id) { return RUNG[id] || cap(id); }
  function clockOf(sec) {
    sec = Math.max(0, Math.round(sec || 0));
    var h = Math.floor(sec / 3600), m = Math.floor(sec / 60) % 60, s = sec % 60;
    return (h ? h + ":" + String(m).padStart(2, "0") : m) + ":" + String(s).padStart(2, "0");
  }
  function minutes(sec) { var m = Math.round((sec || 0) / 60); return m + " min"; }
  function pct(x) { return x == null ? "–" : Math.round(x * 100) + "%"; }
  function dot(state) { return '<span class="sdot sdot--' + esc(state || "unknown") + '" aria-hidden="true"></span>'; }

  // Open the session for the participant of Set up (or Same as last time), once everything is up.
  function beginSession() {
    var last = lastRun();
    ask = { busy: true, error: "", other: "" };
    pollPanel().then(function () {
      var st = ps();
      if (!st) throw new Error(pError || "My control panel isn't answering yet.");
      var cur = st.session || {};
      if (cur.active) {
        if (last && cur.participant !== last.participant) {
          var e = new Error(cur.participant + "'s session is still open in my control panel.");
          e.other = cur.participant;
          throw e;
        }
        return null;
      }
      if (!last) throw new Error("I don't know who's studying. Go back to set up and type the code.");
      return panelPost("session/start", { participant: last.participant }).then(function (res) {
        if (!res.ok) throw new Error(res.message || "The session didn't start.");
      });
    }).then(function () {
      ask = { busy: false, error: "", other: "" };
      startIntent = false;
      desk.seen = ""; desk.was = "";
      location.hash = "#/desk";
    }).catch(function (err) {
      startIntent = false;
      ask = { busy: false, error: err.message, other: err.other || "" };
      if (view === "starting") renderStarting();
    });
  }

  // The bar on top of the desk check and the control panel. Built once; the numbers change in place.
  function sessbar(phase) {
    return '<div class="sessbar" id="sessbar">'
      + '<span id="sb-badge"></span><span class="body" id="sb-who"></span><span class="caption muted" id="sb-time"></span>'
      + '<div style="flex: 1;"></div>'
      + (phase === "control"
        ? '<button type="button" class="ds-btn ds-btn--secondary stopbtn" data-act="stop-eepy" title="Stops my moves and my voice (Esc or Space)">' + icon("Pause", 16) + '<span class="ds-btn__label">Stop EEPY</span><kbd>Esc</kbd></button>'
        : "")
      + '<button type="button" class="ds-btn ds-btn--danger" data-act="end-open" id="sb-end"><span class="ds-btn__label">End session</span></button></div>';
  }
  function updateSessbar(phase) {
    var st = ps(), sess = (st && st.session) || {};
    setHTML($("#sb-badge"), !sess.active ? '<span class="ds-badge">No session</span>'
      : phase === "desk" ? '<span class="ds-badge ds-badge--accent"><span class="ds-badge__dot" aria-hidden="true"></span>Getting ready</span>'
      : '<span class="ds-badge ds-badge--success rec"><span class="ds-badge__dot" aria-hidden="true"></span>Recording</span>');
    setHTML($("#sb-who"), sess.active ? "<b>" + esc(sess.participant) + "</b>" : "");
    setHTML($("#sb-time"), sess.active ? esc(clockOf(sess.elapsed_s) + " · " + labelOf(MODES, sess.mode || st.mode) + " · " + labelOf(BACKENDS, st.backend)) : "");
    var end = $("#sb-end");
    if (end) end.disabled = !sess.active;
  }

  function panelMissing(where) {
    var up = isUp((parts().panel || {}).state);
    return '<div class="empty"><img src="img/eepy-builder.svg" alt=""><h1 class="display-sm">' + (up ? "My control panel isn't answering." : "My control panel isn't running.") + "</h1>"
      + '<p class="body muted">' + esc(pError || "") + (up ? " Give it a second." : " Wake me up first: set up a session.") + "</p>"
      + '<div class="actions"><a class="ds-btn ds-btn--primary" href="#/setup"><span class="ds-btn__label">Go to set up</span></a>'
      + '<a class="ds-btn ds-btn--secondary" href="#/advanced"><span class="ds-btn__label">Advanced tools</span></a></div></div>';
  }
  function noSession() {
    var last = lastRun();
    return '<div class="empty"><img src="img/eepy-wave.png" alt=""><h1 class="display-sm">No session is running.</h1>'
      + '<p class="body muted">' + (sessionName ? "The last one has ended and everything is saved." : "My control panel is awake, but nobody is recording.") + "</p>"
      + '<div class="actions">'
      + (sessionName ? '<a class="ds-btn ds-btn--secondary" href="#/done/' + encodeURIComponent(sessionName) + '"><span class="ds-btn__label">See how it went</span></a>' : "")
      + (last ? '<button type="button" class="ds-btn ds-btn--primary" data-act="begin"><span class="ds-btn__label">Start ' + esc(last.participant) + "'s session</span></button>" : "")
      + '<a class="ds-btn ds-btn--' + (last ? "secondary" : "primary") + '" href="#/setup"><span class="ds-btn__label">Set up a session</span></a></div></div>';
  }

  // --- the desk check (S2b): the panel's calibration, before the study starts --------------------

  var STAGE = { start: "Getting started", face: "Their face", gaze: "Where they look", baseline: "One minute of normal work", done: "Done" };
  var SKIP = ["Short of time", "The camera isn't working", "Just testing", "Another reason"];

  function renderDesk() {
    desk.seen = "";
    main.innerHTML = sessbar("desk") + '<section class="desk" id="desk"></section>';
    updateDesk();
  }

  function updateDesk() {
    var box = $("#desk");
    if (!box) return;
    updateSessbar("desk");
    var st = ps();
    if (!st) { setHTML(box, panelMissing()); desk.seen = ""; return; }
    var sess = st.session || {};
    if (!sess.active) { setHTML(box, noSession()); desk.seen = ""; return; }
    sessionName = sess.session || sessionName;
    var cal = st.calibration || { state: "none" };
    var key = [cal.state, cal.stage, cal.step, cal.progress, cal.instruction, cal.message, cal.vision_connected, desk.busy, desk.msg, !!cal.reuse].join("|");
    if (key === desk.seen) return;               // nothing new: keep the reason picked and the focus
    var finished = ["done", "reused", "skipped"].indexOf(cal.state) >= 0;
    if (finished && desk.was && desk.was !== cal.state) {
      setTimeout(function () { if (view === "desk") location.hash = "#/control"; }, 1800);
    }
    desk.was = cal.state;
    desk.seen = key;
    box.innerHTML = deskBody(cal, sess, st);
  }

  function deskHead(eyebrow, title, text) {
    return '<header class="deskhead"><span class="caption eyebrow">' + eyebrow + '</span><h1 class="display-md" aria-live="polite">' + title + "</h1>"
      + (text ? '<p class="body-lg muted">' + text + "</p>" : "") + "</header>";
  }

  function skipCard(primary) {
    return '<section class="card opt"><div class="cardhead"><span class="ic">' + icon("ArrowRight", 18) + '</span><span class="headline">Skip it this time</span></div>'
      + '<p class="body-sm muted">I\'ll use my default settings, so my guesses about their focus will be rougher.</p>'
      + '<div class="ds-field"><label class="ds-field__label" for="skip-reason">Why skip? (saved with the session)</label><div class="ds-field__control ds-select">'
      + '<select id="skip-reason" class="ds-input ds-select__el">' + SKIP.map(function (r) {
        return "<option" + (r === desk.reason ? " selected" : "") + ">" + esc(r) + "</option>";
      }).join("") + "</select>" + icon("ChevronRight", 16).replace('class="ds-icon"', 'class="ds-icon ds-select__chev"') + "</div></div>"
      + '<button type="button" class="ds-btn ds-btn--' + (primary ? "primary" : "secondary") + '" data-act="cal" data-value="skip"' + (desk.busy ? " disabled" : "") + '><span class="ds-btn__label">Skip the check</span></button></section>';
  }

  function deskBody(cal, sess, st) {
    var who = esc(sess.participant);
    var error = desk.msg ? '<p class="body-sm bad-text" role="alert">' + icon("CircleAlert", 16) + esc(desk.msg) + "</p>" : "";
    var eyes = !!cal.vision_connected;
    var state = cal.state;
    if (state === "requested" || state === "running") {
      var stage = STAGE[cal.stage] || (state === "requested" ? "Getting ready" : "Checking");
      var step = cal.step ? " · step " + esc(cal.step) : "";
      var frac = Math.max(0, Math.min(1, Number(cal.progress) || 0));
      return deskHead("Run a session · step 2 of 3 · desk check" + step, state === "requested" ? "Getting ready…" : esc(stage),
          state === "requested" ? esc(cal.message || "I'm waiting for my eyes to start the check.") : "They follow the full-screen window on this computer. You just keep an eye on it.")
        + '<section class="card deskrun">'
        + '<div class="progress" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="' + Math.round(frac * 100) + '"><span style="width: ' + (frac * 100).toFixed(0) + '%;"></span></div>'
        + (cal.instruction ? '<p class="headline">"' + esc(cal.instruction) + '"</p><span class="caption muted">What they see right now</span>' : "")
        + (cal.message ? '<p class="body-sm">' + esc(cal.message) + "</p>" : "")
        + error
        + '<div class="actions"><button type="button" class="ds-btn ds-btn--secondary" data-act="cal" data-value="stop"' + (desk.busy ? " disabled" : "") + '><span class="ds-btn__label">Stop the check</span></button></div>'
        + '<span class="caption muted">Stopping saves nothing: you can start it again or skip it.</span></section>';
    }
    if (state === "done" || state === "reused" || state === "skipped") {
      var title = { done: "All set: I know this desk now.", reused: "All set: I'm using the last check.", skipped: "Skipped: I'll use my default settings." }[state];
      return deskHead("Run a session · step 2 of 3 · desk check", title, "Next: the control panel. The study starts now.")
        + '<div class="actions"><a class="ds-btn ds-btn--primary btn-big" href="#/control"><span class="ds-btn__label">Go to the control panel</span>' + icon("ArrowRight", 16) + "</a></div>";
    }
    var failed = state === "failed", stopped = state === "stopped";
    var reuse = cal.reuse;
    var head = failed ? deskHead("Run a session · step 2 of 3 · desk check", "The check didn't work.", esc(cal.message || "Something went wrong.") + " Nothing was saved, so trying again starts from the beginning.")
      : deskHead("Run a session · step 2 of 3 · desk check", "Before we start, let me learn " + who + "'s desk.",
        stopped ? "The check was stopped and nothing was saved. Start it again, or skip it." : "So I can tell focus from drifting off for this person, at this desk.");
    var why = '<div class="why">'
      + '<div><span class="ic">' + icon("Search", 18) + '</span><span class="body-sm">Their face and where they look: the notes, the screen, or away</span></div>'
      + '<div><span class="ic">' + icon("BookOpen", 18) + '</span><span class="body-sm">How they look when they simply work</span></div>'
      + '<div><span class="ic">' + icon("AlarmClock", 18) + '</span><span class="body-sm">About two minutes, before the study starts</span></div></div>';
    var cards = [];
    if (reuse && !failed) {
      var age = reuse.age_days == null ? "" : reuse.age_days < 1 ? "today" : Math.round(reuse.age_days) === 1 ? "yesterday" : Math.round(reuse.age_days) + " days ago";
      cards.push('<section class="card opt"><div class="cardhead"><span class="ic ok">' + icon("Check", 18) + '</span><span class="headline">Use my last check</span></div>'
        + '<p class="body-sm muted">I checked ' + who + "'s desk " + esc(age) + (reuse.late_evening ? ", late in the evening" : "") + ". Same desk and light? Then this is the quickest.</p>"
        + '<button type="button" class="ds-btn ds-btn--primary" data-act="cal" data-value="reuse"' + (desk.busy ? " disabled" : "") + '><span class="ds-btn__label">Use it</span></button></section>');
    }
    cards.push('<section class="card opt"><div class="cardhead"><span class="ic">' + icon("Search", 18) + '</span><span class="headline">' + (failed || stopped ? "Check again" : "Check the desk now") + "</span></div>"
      + '<ol class="body-sm muted steplist"><li>Their face: they look at the screen</li><li>Their eyes: they follow a dot, full screen</li><li>One minute of normal work, in a small window</li></ol>'
      + (eyes ? "" : '<p class="caption bad-text">' + icon("CircleAlert", 14) + "My eyes aren't connected yet, so I can't check. Wait a moment, or skip.</p>")
      + '<button type="button" class="ds-btn ds-btn--' + (reuse && !failed ? "secondary" : "primary") + '" data-act="cal" data-value="calibrate"' + (desk.busy || !eyes ? " disabled" : "") + '><span class="ds-btn__label">' + (failed || stopped ? "Try again" : "Start the check") + "</span></button></section>");
    cards.push(skipCard(false));
    return head + why + error + '<div class="opts">' + cards.join("") + "</div>";
  }

  function deskCommand(action) {
    var body = { action: action };
    if (action === "skip") body.reason = desk.reason;
    desk.busy = true; desk.msg = "";
    updateDesk();
    panelPost("calibration", body).then(function (res) {
      desk.busy = false;
      if (!res.ok) desk.msg = res.message || "That didn't work.";
      return pollPanel();
    }).then(function () { if (view === "desk") updateDesk(); });
  }

  // --- the control panel (S2) --------------------------------------------------------------------

  function renderControl() {
    ctrl.version = null;
    main.innerHTML = sessbar("control") + '<div id="banners" class="banners"></div><section class="control" id="control"></section>';
    updateControl();
    loadVolume();
  }

  function loadVolume() {
    if (!status) { refresh().then(function () { if (status && view === "control") loadVolume(); }); return; }
    if (status.settings.backend === "dry") { volumeInfo = { ok: false, message: "no robot in this setting, so there is no speaker to set." }; drawVolume(); return; }
    getJSON("/api/v5/volume").then(function (data) { volumeInfo = data; drawVolume(); }).catch(function () { volumeInfo = null; drawVolume(); });
  }

  function controlSkeleton(st, panel) {
    var groups = {};
    (panel.groups || []).forEach(function (g) { groups[g.id] = g; });
    buttons = {}; keys = {};
    (panel.groups || []).forEach(function (g) {
      g.buttons.forEach(function (b) { buttons[b.id] = b; if (b.key) keys[String(b.key).toLowerCase()] = b; });
    });
    if (ctrl.intensity == null) ctrl.intensity = panel.default_intensity || 0.7;
    var tiles = function (g) {
      return g ? g.buttons.map(function (b, i) {
        return '<button type="button" class="tile" data-act="press" data-id="' + esc(b.id) + '" data-target="' + esc(b.target) + '">'
          + '<span class="tile__rung">' + i + '</span><span class="tile__name">' + esc(behaviorName(b.target)) + "</span>"
          + (b.key ? "<kbd>" + esc(String(b.key).toUpperCase()) + "</kbd>" : "") + "</button>";
      }).join("") : "";
    };
    var chips = function (g, kind) {
      return g ? g.buttons.map(function (b) {
        var name = kind === "marker" ? (MARKER[b.target] || cap(b.label)) : cap(b.label);
        return '<button type="button" class="chip chip--key" data-act="press" data-id="' + esc(b.id) + '">' + esc(name)
          + (b.key ? " <kbd>" + esc(String(b.key).toUpperCase()) + "</kbd>" : "") + "</button>";
      }).join("") : "";
    };
    var nod = groups.basics && groups.basics.buttons.filter(function (b) { return b.kind === "behavior"; });
    var classic = (parts().panel || {}).url;
    return '<div class="control__grid">'
      + '<section class="card now" id="now"></section>'
      + '<section class="ctrlmain"><div class="ctrltabs"><div class="ds-tabs" role="tablist">'
      + '<button type="button" role="tab" class="ds-tab' + (ctrl.tab === "controls" ? " is-selected" : "") + '" data-act="tab" data-value="controls" aria-selected="' + (ctrl.tab === "controls") + '">Your controls</button>'
      + '<button type="button" role="tab" class="ds-tab' + (ctrl.tab === "hmm" ? " is-selected" : "") + '" data-act="tab" data-value="hmm" aria-selected="' + (ctrl.tab === "hmm") + '">Live HMM</button></div>'
      + '<p class="caption" id="ctrl-msg" aria-live="polite"></p></div>'
      + '<div id="tab-controls"' + (ctrl.tab === "controls" ? "" : " hidden") + ' class="ctrlcards">'
      + '<section class="card"><div class="cardhead"><span class="ic">' + icon("Zap", 18) + '</span><span class="headline">Move me</span>'
      + '<span style="flex: 1;"></span><label class="caption muted" for="intensity">How big</label><input id="intensity" type="range" min="0.2" max="1" step="0.1" value="' + ctrl.intensity + '" class="range"><span class="caption" id="intensity-v">' + Math.round(ctrl.intensity * 100) + "%</span></div>"
      + (groups.refocus ? '<div class="ladder"><span class="caption eyebrow">Focus · gentle to strong</span><div class="tiles">' + tiles(groups.refocus) + "</div></div>" : "")
      + (groups.bedtime ? '<div class="ladder"><span class="caption eyebrow">Tiredness · gentle to strong</span><div class="tiles">' + tiles(groups.bedtime) + "</div></div>" : "")
      + '<div class="ctrlrow">' + (nod && nod.length ? nod.map(function (b) {
        return '<button type="button" class="chip chip--key" data-act="press" data-id="' + esc(b.id) + '" data-target="' + esc(b.target) + '">' + esc(behaviorName(b.target)) + (b.key ? " <kbd>" + esc(String(b.key).toUpperCase()) + "</kbd>" : "") + "</button>";
      }).join("") : "")
      + '<span style="flex: 1;"></span><div class="ds-switch"><button type="button" role="switch" class="ds-switch__track" id="idle" data-act="idle" aria-checked="false" aria-labelledby="idle-l"><span class="ds-switch__thumb"></span></button>'
      + '<span class="ds-check__label" id="idle-l">Breathe while waiting</span></div><kbd>' + esc(String(panel.idle_key || "a").toUpperCase()) + "</kbd></div></section>"
      + '<section class="card"><div class="cardhead"><span class="ic">' + icon("Mail", 18) + '</span><span class="headline">Talk</span><span style="flex: 1;"></span>'
      + '<div class="seg" role="radiogroup" aria-label="Tone"><button type="button" role="radio" data-act="tone" data-value="hedged" id="tone-hedged">Gentle</button><button type="button" role="radio" data-act="tone" data-value="direct" id="tone-direct">Direct</button></div>'
      + '<kbd title="Switch the tone">' + esc(KEYNAME[panel.tone_key] || panel.tone_key || "Tab") + "</kbd></div>"
      + '<div class="chips">' + chips(groups.lines, "line") + "</div>"
      + '<form class="sayrow" id="say-form" autocomplete="off"><input id="say-text" class="ds-input" type="text" maxlength="300" placeholder="Or type something for me to say…" aria-label="Something for me to say">'
      + '<button type="submit" class="ds-btn ds-btn--secondary"><span class="ds-btn__label">Say it</span></button></form>'
      + '<div class="ctrlrow" id="volume"></div></section>'
      + '<section class="card"><div class="cardhead"><span class="ic">' + icon("Search", 18) + '</span><span class="headline">What did you see?</span>'
      + '<span class="caption muted">Marks the moment in the recording.</span></div><div class="chips">' + chips(groups.markers, "marker") + "</div></section>"
      + "</div>"
      + '<div id="tab-hmm"' + (ctrl.tab === "hmm" ? "" : " hidden") + ' class="card hmm"></div>'
      + '<div class="ctrlfoot"><button type="button" class="ds-btn ds-btn--secondary" data-act="log-toggle" id="log-btn"><span class="ds-btn__label">Session log</span></button>'
      + '<button type="button" class="ds-btn ds-btn--secondary" data-act="note-toggle"><span class="ds-btn__label">Add a note</span></button>'
      + '<form id="note-form" class="sayrow"' + (ctrl.noteOpen ? "" : " hidden") + ' autocomplete="off"><input id="note-text" class="ds-input" type="text" maxlength="2000" placeholder="What happened? (saved in the session, with the time)" aria-label="Note">'
      + '<button type="submit" class="ds-btn ds-btn--secondary"><span class="ds-btn__label">Save note</span></button></form>'
      + '<span style="flex: 1;"></span>' + (classic ? '<a class="caption" href="' + esc(classic) + '" target="_blank" rel="noopener">Open the classic panel</a>' : "") + "</div>"
      + '<div class="card sessionlog" id="sessionlog"' + (ctrl.logOpen ? "" : " hidden") + "></div>"
      + "</section></div>";
  }

  function updateControl() {
    var box = $("#control");
    if (!box) return;
    updateSessbar("control");
    var st = ps();
    if (!st) { setHTML(box, panelMissing()); ctrl.version = null; setHTML($("#banners"), ""); return; }
    var sess = st.session || {};
    if (!sess.active) { setHTML(box, noSession()); ctrl.version = null; setHTML($("#banners"), ""); return; }
    sessionName = sess.session || sessionName;
    if (ctrl.version !== st.panel_version || !$("#now")) {
      ctrl.version = st.panel_version;
      box.innerHTML = controlSkeleton(st, pstate.panel || {});
      box.__html = null;
      drawVolume();
      drawHmm();
    }
    setHTML($("#now"), rightNow(st));
    setHTML($("#banners"), banners(st));
    document.querySelectorAll(".tile, .chip--key").forEach(function (el) {
      el.classList.toggle("is-playing", !!st.playing && el.getAttribute("data-target") === st.playing);
    });
    var idle = $("#idle");
    if (idle) idle.setAttribute("aria-checked", st.idle_on ? "true" : "false");
    ["hedged", "direct"].forEach(function (t) {
      var b = $("#tone-" + t);
      if (b) { b.classList.toggle("is-on", ctrl.tone === t); b.setAttribute("aria-checked", ctrl.tone === t ? "true" : "false"); }
    });
    var msg = $("#ctrl-msg");
    if (msg) { msg.textContent = ctrl.msg; msg.style.color = ctrl.msgBad ? "var(--error)" : "var(--ink-muted)"; }
    if (ctrl.logOpen) setHTML($("#sessionlog"), sessionLog());
  }

  function rightNow(st) {
    var us = st.user_state, au = st.autonomy || {}, cal = st.calibration || {};
    var out = '<div class="cardhead"><span class="ic">' + icon("Search", 18) + '</span><span class="headline">Right now</span></div>';
    if (!us) {
      out += '<p class="body muted">' + (cal.vision_connected ? "I'm looking… my first guess comes in a few seconds." : "I can't see them: my eyes aren't connected.") + "</p>";
    } else {
      var probs = us.probs || {};
      out += '<div class="nowstate">' + dot(us.state) + '<span class="display-sm">' + esc(STATE_NAME[us.state] || cap(us.state)) + "</span></div>"
        + '<span class="caption muted">' + (us.confidence != null ? "I'm " + pct(us.confidence) + " sure" : "") + (us.estimator ? " · " + esc(us.estimator === "hmm" ? "my estimate" : "simple rules") : "")
        + (us.fresh === false ? " · <b>not updating</b>" : "") + (us.calibrated === false ? " · no desk check" : "") + "</span>"
        + (us.pending ? '<span class="caption">Maybe becoming: ' + esc(STATE_NAME[us.pending] || cap(us.pending)) + "</span>" : "")
        + (us.reason ? '<p class="body-sm muted" style="margin: 0;">' + esc(us.reason) + "</p>" : "");
      var shown = STATE_ORDER.filter(function (k) { return probs[k] != null; });
      if (shown.length) {
        out += '<div class="bars">' + shown.map(function (k) {
          return '<div class="bar"><span class="caption">' + esc(STATE_NAME[k] || cap(k)) + '</span><span class="bar__track"><span class="bar__fill sbg--' + k + '" style="width: ' + (Math.max(0, Math.min(1, probs[k])) * 100).toFixed(0) + '%;"></span></span><span class="caption muted">' + pct(probs[k]) + "</span></div>";
        }).join("") + "</div>";
      }
    }
    out += '<div class="nowsplit"><span class="caption eyebrow">What I\'m doing</span>';
    if (!au.active) {
      out += '<p class="body-sm muted" style="margin: 0;">' + ((st.mode || "") === "wizard" ? "You call the shots: I only do what you press." : esc(cap(au.next || "I'm not deciding anything yet."))) + "</p>";
    } else {
      var doing = au.paused ? "Paused: I won't step in until you let me." : au.ladder ? (au.ladder === "refocus" ? "Helping them refocus" : "Helping them wind down") + (au.level != null ? " · step " + esc(au.level) : "") : "Watching quietly";
      out += '<p class="body" style="margin: 0;">' + doing + "</p>"
        + (au.next ? '<span class="caption muted">Next: ' + esc(au.next) + (au.next_in != null ? " (in " + esc(clockOf(au.next_in)) + ")" : "") + "</span>" : "")
        + (au.explanation && au.explanation.sentence ? '<p class="body-sm" style="margin: 0;">"' + esc(au.explanation.sentence) + '"</p>' : "")
        + (au.bandit && au.bandit.sentence ? '<p class="caption muted" style="margin: 0;">' + esc(au.bandit.sentence) + "</p>" : "")
        + '<button type="button" class="ds-btn ds-btn--secondary" data-act="autonomy" data-value="' + (au.paused ? "resume" : "pause") + '" style="align-self: flex-start;"><span class="ds-btn__label">' + (au.paused ? "Let me step in again" : "Hold me back") + "</span></button>";
    }
    return out + "</div>";
  }

  function banners(st) {
    var out = [];
    var b = function (text, act, label) {
      return '<div class="banner" role="status">' + icon("CircleAlert", 18) + '<span class="body-sm">' + text + "</span>"
        + (act ? '<button type="button" class="ds-btn ds-btn--secondary" data-act="' + act + '"><span class="ds-btn__label">' + label + "</span></button>" : "") + "</div>";
    };
    if (!st.connected) out.push(b("I've lost my body" + (st.connection_error ? ": " + esc(st.connection_error) : ".") + " Moves and speech won't work until I reconnect.", "reconnect", "Reconnect"));
    var cal = st.calibration || {};
    if (st.mode !== "wizard" && !cal.vision_connected) out.push(b("My eyes aren't connected, so I can't see how they're doing. Check “My eyes” in Advanced tools."));
    else if (st.user_state && st.user_state.fresh === false) out.push(b("My eyes stopped sending updates: what you see under Right now may be old."));
    if (st.session && st.session.write_error) out.push(b("I can't save the session: " + esc(st.session.write_error)));
    if (st.last_error) out.push(b(esc(st.last_error)));
    return out.join("");
  }

  function humanLog(e) {
    var t = String(e.t_wall || "").slice(11, 19);
    var text;
    switch (e.type) {
      case "behavior_started": text = (e.source === "auto" ? "I moved by myself: " : "I moved: ") + behaviorName(e.behavior); break;
      case "behavior_interrupted": text = "I stopped " + behaviorName(e.behavior) + (e.by ? " (" + cap(e.by) + ")" : ""); break;
      case "speech_requested": text = 'I said: "' + (e.text || "") + '"'; break;
      case "annotation": text = "You marked: " + (MARKER[e.marker] || cap(e.label || e.marker)); break;
      case "note": text = "Note: " + (e.text || ""); break;
      case "nudge_decision": text = "I stepped in: " + behaviorName(e.behavior); break;
      case "nudge_outcome": text = "How it went: " + (OUTCOME[e.outcome] || cap(e.outcome)); break;
      case "nudge_reward": text = "How your nudge went: " + (OUTCOME[e.outcome] || cap(e.outcome)); break;
      case "calibration": text = "Desk check: " + cap(e.result); break;
      case "escalation": text = cap(e.what); break;
      case "session_start": text = "The session started"; break;
      case "session_end": text = "The session ended"; break;
      case "wizard_command": text = "You pressed: " + cap(e.action) + (e.behavior ? " · " + behaviorName(e.behavior) : e.line_id ? " · " + cap(e.line_id) : ""); break;
      default: text = cap(e.type);
    }
    return '<li><span class="caption muted">' + esc(t) + '</span><span class="body-sm">' + esc(text) + "</span></li>";
  }
  function sessionLog() {
    var rows = ((pstate && pstate.log) || []).slice().reverse();
    return '<div class="cardhead"><span class="headline">Session log</span><span class="caption muted">The last 20 things that happened, newest first.</span></div>'
      + (rows.length ? '<ol class="loglist">' + rows.map(humanLog).join("") + "</ol>" : '<p class="body-sm muted">Nothing yet.</p>');
  }

  function drawVolume() {
    var box = $("#volume");
    if (!box) return;
    if (volumeInfo && volumeInfo.ok && volumeInfo.volume != null) {
      box.innerHTML = '<label class="caption muted" for="vol">My volume' + (volumeInfo.simulator ? " (this computer's)" : "") + "</label>"
        + '<input id="vol" type="range" min="0" max="100" step="5" value="' + Number(volumeInfo.volume) + '" class="range" style="flex: 1;">'
        + '<span class="caption" id="vol-v">' + Number(volumeInfo.volume) + "%</span>";
    } else {
      box.innerHTML = '<span class="caption muted">Volume: ' + esc((volumeInfo && volumeInfo.message) || "not available right now.") + "</span>";
    }
  }

  function drawHmm() {
    var box = $("#tab-hmm");
    if (!box) return;
    var vision = parts().vision || {};
    if (vision.url && isUp(vision.state)) {
      box.innerHTML = '<div class="cardhead"><span class="headline">What I think, live</span><span class="caption muted">Each circle fills up with how likely that state is. From my eyes\' live page.</span>'
        + '<span style="flex: 1;"></span><a class="caption" href="' + esc(vision.url) + '" target="_blank" rel="noopener">Open in its own window</a></div>'
        + '<iframe class="hmmframe" src="' + esc(vision.url) + '" title="The live HMM page"></iframe>';
    } else {
      box.innerHTML = '<div class="empty"><h2 class="headline">My eyes aren\'t running.</h2><p class="body-sm muted">The live HMM comes from my eyes\' live page. Start “My eyes” in Advanced tools.</p></div>';
    }
  }

  function flash(text, bad) {
    ctrl.msg = text; ctrl.msgBad = !!bad;
    var msg = $("#ctrl-msg");
    if (msg) { msg.textContent = text; msg.style.color = bad ? "var(--error)" : "var(--ink-muted)"; }
    clearTimeout(flash.t);
    flash.t = setTimeout(function () { ctrl.msg = ""; var m = $("#ctrl-msg"); if (m) m.textContent = ""; }, 5000);
  }
  function answer(res, ok) {
    if (res && res.ok) flash(ok(res), false);
    else flash((res && res.message) || "That didn't work.", true);
    pollPanel().then(function () { if (view === "control") updateControl(); });
  }

  function press(b, via) {
    if (!b) return;
    if (b.kind === "behavior") {
      panelPost("play", { behavior: b.target, intensity: ctrl.intensity, via: via }).then(function (r) { answer(r, function () { return "Playing: " + behaviorName(b.target); }); });
    } else if (b.kind === "line") {
      panelPost("say", { line_id: b.target, tone: ctrl.tone, via: via }).then(function (r) { answer(r, function (x) { return 'Saying: "' + (x.text || cap(b.label)) + '"'; }); });
    } else if (b.kind === "marker") {
      panelPost("annotate", { marker: b.target, via: via }).then(function (r) { answer(r, function () { return "Marked: " + (MARKER[b.target] || cap(b.label)); }); });
    }
  }
  function stopEepy(via) {
    panelPost("stop", { via: via }).then(function (r) { answer(r, function () { return "Stopped: no moves, no voice."; }); });
  }

  // --- ending the session ----------------------------------------------------------------------

  function openModal(html) {
    closeModal();
    var wrap = document.createElement("div");
    wrap.className = "modalwrap";
    wrap.id = "modal";
    wrap.innerHTML = '<div class="ds-modal-scrim" data-act="modal-close"></div>' + html;
    document.body.appendChild(wrap);
    var first = wrap.querySelector("textarea, input, .ds-btn--secondary");
    if (first) first.focus();
  }
  function closeModal() { var m = $("#modal"); if (m) m.remove(); }
  function modalHead(title, desc) {
    return '<div class="ds-modal__head"><h2 class="ds-modal__title" id="m-title">' + title + "</h2>"
      + '<button type="button" class="ds-iconbtn ds-modal__close" data-act="modal-close" aria-label="Close">' + icon("X", 18) + "</button></div>"
      + (desc ? '<p class="ds-modal__desc">' + desc + "</p>" : "");
  }

  function openEnd() {
    var sess = (ps() && ps().session) || {};
    if (!sess.active) return;
    openModal('<div class="ds-modal" role="dialog" aria-modal="true" aria-labelledby="m-title">'
      + modalHead("End " + esc(sess.participant) + "'s session?", "I stop recording, save everything and go back to neutral. This can't be undone, but you can start a new session right after.")
      + '<div class="ds-modal__body"><div class="ds-field"><label class="ds-field__label" for="end-notes">Anything to remember? (optional)</label>'
      + '<div class="ds-field__control"><textarea id="end-notes" class="ds-input ds-textarea" maxlength="2000" placeholder="Saved in the session folder"></textarea></div></div>'
      + '<p class="body-sm bad-text" id="end-msg" hidden></p></div>'
      + '<div class="ds-modal__actions"><button type="button" class="ds-btn ds-btn--secondary" data-act="modal-close"><span class="ds-btn__label">Keep going</span></button>'
      + '<button type="button" class="ds-btn ds-btn--danger" data-act="end-confirm"><span class="ds-btn__label">End session</span></button></div></div>');
  }

  function endSession(btn) {
    var sess = (ps() && ps().session) || {};
    var name = sess.session || sessionName;
    var notes = ($("#end-notes") || {}).value || "";
    btn.disabled = true;
    btn.querySelector(".ds-btn__label").textContent = "Saving…";
    panelPost("session/end", { notes: notes.trim() || null }).then(function (res) {
      if (!res.ok) {
        btn.disabled = false;
        btn.querySelector(".ds-btn__label").textContent = "End session";
        var m = $("#end-msg"); if (m) { m.hidden = false; m.textContent = res.message || "The session didn't end."; }
        return;
      }
      closeModal();
      sessionName = name;
      location.hash = "#/done/" + encodeURIComponent(name);
    });
  }

  // --- the session is over (S3) ------------------------------------------------------------------

  function renderDone(name) {
    main.innerHTML = '<p class="body muted" style="padding: 80px;">Putting it together…</p>';
    getJSON("/api/v5/session?name=" + encodeURIComponent(name)).then(function (s) {
      if (view !== "done") return;
      if (!s || !s.name) { main.innerHTML = '<div class="empty"><h1 class="display-sm">I can\'t find that session.</h1><a class="ds-btn ds-btn--primary" href="#/sessions"><span class="ds-btn__label">See every session</span></a></div>'; return; }
      return getJSON("/api/v5/progress?participant=" + encodeURIComponent(s.participant)).then(function (p) { drawDone(s, p); }, function () { drawDone(s, null); });
    }).catch(function () { main.innerHTML = '<div class="empty"><h1 class="display-sm">The launcher did not answer.</h1></div>'; });
  }

  function worked(s) { return (s.outcomes && s.outcomes.success) || 0; }

  function drawDone(s, p) {
    if (view !== "done") return;
    var ending = {
      rest: ["They stopped to rest. Good call!", "I suggested winding down, and they went to rest. That's the best ending."],
      suggested: ["I suggested a rest, and you ended here.", "I'd noticed they were getting tired and said so."],
      you: ["Session complete.", "You ended the session. Everything is saved."]
    }[s.ending] || ["Session complete.", "Everything is saved."];
    var nudges = s.nudges + s.by_hand;
    var tiles = [
      ["AlarmClock", "Time together", minutes(s.duration_s), esc(s.clock + "–" + s.end_clock)],
      ["Search", "Focused", s.has_state && s.focused_share != null ? pct(s.focused_share) : "–",
        s.has_state && s.desk_s ? minutes(s.focused_s) + " of " + minutes(s.desk_s) + " at the desk" : "My eyes weren't on, so no focus numbers."],
      ["Zap", "I stepped in", nudges + (nudges === 1 ? " time" : " times"),
        nudges ? worked(s) + " worked" + (s.by_hand ? " · " + s.by_hand + " by you" : "") : "Not once."],
      ["Check", "Time won back", p && p.enough ? (p.gain_points >= 0 ? "+" : "") + p.gain_points + " points" : "Soon",
        p && p.enough ? "About " + (p.minutes_more >= 0 ? "+" : "") + p.minutes_more + " min focused vs their first 3 sessions" : "After 4 sessions with my eyes on, I'll compare."]
    ];
    main.innerHTML = '<section class="done">'
      + '<div class="done__hero"><img class="bob" src="img/eepy-wave.png" alt=""><div><span class="caption eyebrow">Run a session · step 3 of 3 · wrap up · ' + esc(s.participant) + "</span>"
      + '<h1 class="display-md">' + ending[0] + '</h1><p class="body-lg muted">' + ending[1] + "</p></div></div>"
      + '<div class="stats">' + tiles.map(function (t) {
        return '<div class="card stat"><span class="cardhead"><span class="ic">' + icon(t[0], 16) + '</span><span class="caption muted">' + t[1] + '</span></span><span class="display-sm">' + t[2] + '</span><span class="caption muted">' + t[3] + "</span></div>";
      }).join("") + "</div>"
      + '<a class="card linkcard" href="#/story/' + encodeURIComponent(s.name) + '"><span class="ic">' + icon("BookOpen", 18) + '</span><span style="flex: 1; display: flex; flex-direction: column;"><span class="headline">See the whole story</span>'
      + '<span class="body-sm muted">Their focus minute by minute, every time I stepped in, and why.</span></span>' + icon("ArrowRight", 18) + "</a>"
      + '<div class="actions"><button type="button" class="ds-btn ds-btn--secondary" data-act="shutdown"><span class="ds-btn__label">Shut me down</span></button>'
      + '<a class="ds-btn ds-btn--primary" href="#/setup"><span class="ds-btn__label">Next participant</span>' + icon("ArrowRight", 16) + "</a></div>"
      + '<span class="caption muted" id="done-msg"></span></section>';
  }

  // --- Analyze: Sessions (A1), Session story (A2), What I learned (A3) ----------------------------

  var analyze = { who: "", list: null, story: null, open: -1, learned: null, bucket: "", busy: false, msg: "" };

  function tabs(on, storyName) {
    var t = function (href, text, key) {
      return '<a class="ds-tab' + (on === key ? " is-selected" : "") + '" href="' + href + '"' + (on === key ? ' aria-current="page"' : "") + ">" + text + "</a>";
    };
    return '<header class="pagehead"><div class="pagehead__text"><span class="caption eyebrow">Analyze</span><h1 class="display-md">' + { sessions: "How we did", story: "One session, step by step", learned: "What I learned" }[on] + "</h1></div>"
      + '<nav class="ds-tabs" aria-label="Analyze">' + t("#/sessions", "Sessions", "sessions")
      + t(storyName ? "#/story/" + encodeURIComponent(storyName) : "#/sessions", "Session story", "story") + t("#/learned", "What I learned", "learned") + "</nav></header>";
  }

  function whoPicker(list, value, everyone) {
    return '<div class="ds-field picker"><label class="ds-field__label" for="who">Participant</label><div class="ds-field__control ds-select">'
      + '<select id="who" class="ds-input ds-select__el">' + (everyone ? '<option value="">Everyone</option>' : "") + list.map(function (c) {
        return '<option value="' + esc(c) + '"' + (c === value ? " selected" : "") + ">" + esc(c) + "</option>";
      }).join("") + "</select>" + icon("ChevronRight", 16).replace('class="ds-icon"', 'class="ds-icon ds-select__chev"') + "</div></div>";
  }

  function strip(runs, total, small) {
    total = Math.max(1, total);
    return '<div class="strip' + (small ? " strip--sm" : "") + '" aria-hidden="true">' + (runs || []).map(function (r) {
      return '<span class="sbg--' + esc(r.state) + '" style="left: ' + (r.from / total * 100).toFixed(2) + "%; width: " + (Math.max(0.3, (r.to - r.from) / total * 100)).toFixed(2) + '%;" title="' + esc((STATE_NAME[r.state] || r.state) + " · " + minutes(r.to - r.from)) + '"></span>';
    }).join("") + "</div>";
  }
  function legend(runs) {
    var seen = {};
    (runs || []).forEach(function (r) { seen[r.state] = true; });
    return '<div class="legend">' + STATE_ORDER.filter(function (k) { return seen[k]; }).map(function (k) {
      return '<span class="caption">' + dot(k) + esc(STATE_NAME[k]) + "</span>";
    }).join("") + "</div>";
  }
  function dayOf(s) {
    try { return new Date(s.start).toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" }); } catch (e) { return s.date; }
  }

  function renderSessions() {
    main.innerHTML = tabs("sessions") + '<section class="analyze" id="analyze"><p class="body muted">Reading the sessions…</p></section>';
    getJSON("/api/v5/sessions").then(function (data) {
      analyze.list = data.sessions || [];
      drawSessions();
    }).catch(function () { $("#analyze").innerHTML = '<p class="body bad-text">The launcher did not answer.</p>'; });
  }

  function drawSessions() {
    var box = $("#analyze");
    if (!box || !analyze.list) return;
    var all = analyze.list, codes = [];
    all.forEach(function (s) { if (s.participant && codes.indexOf(s.participant) < 0) codes.push(s.participant); });
    codes.sort();
    if (analyze.who && codes.indexOf(analyze.who) < 0) analyze.who = "";
    var rows = all.filter(function (s) { return !analyze.who || s.participant === analyze.who; });
    if (!all.length) {
      box.innerHTML = '<div class="empty"><img src="img/eepy-scientist.svg" alt=""><h2 class="display-sm">No sessions yet.</h2><p class="body muted">Once you run a session, it shows up here.</p><a class="ds-btn ds-btn--primary" href="#/setup"><span class="ds-btn__label">Run a session</span></a></div>';
      return;
    }
    box.innerHTML = '<div class="analyze__top">' + whoPicker(codes, analyze.who, true) + '<div class="card won" id="won"></div></div>'
      + '<ol class="sesslist">' + rows.map(function (s) {
        var total = s.duration_s || 1;
        var n = s.nudges + s.by_hand;
        return '<li><a class="card sesscard" href="#/story/' + encodeURIComponent(s.name) + '">'
          + '<div class="sesscard__head"><b class="body">' + esc(s.participant) + '</b><span class="body-sm muted">' + esc(dayOf(s)) + " · " + esc(s.clock) + "–" + esc(s.end_clock) + "</span>"
          + '<span class="ds-badge">' + esc(labelOf(MODES, s.mode)) + "</span>" + (s.ended ? "" : '<span class="ds-badge ds-badge--accent">Not ended</span>')
          + '<span style="flex: 1;"></span><span class="body-sm">' + (s.has_state ? "<b>" + pct(s.focused_share) + "</b> focused · " : "") + minutes(s.duration_s) + " · " + n + (n === 1 ? " nudge" : " nudges") + "</span>" + icon("ChevronRight", 16) + "</div>"
          + (s.has_state ? strip(s.strip, total, true) : '<span class="caption muted">No eye data in this session.</span>')
          + "</a></li>";
      }).join("") + "</ol>";
    var won = $("#won");
    if (!analyze.who) { won.innerHTML = '<span class="caption muted">Pick a participant to see the time we won back together.</span>'; return; }
    getJSON("/api/v5/progress?participant=" + encodeURIComponent(analyze.who)).then(function (p) {
      if (!$("#won")) return;
      $("#won").innerHTML = p.enough
        ? '<span class="caption eyebrow">Time won back · ' + esc(p.participant) + '</span><span class="display-sm">' + (p.gain_points >= 0 ? "+" : "") + p.gain_points + " points</span>"
          + '<span class="body-sm muted">' + pct(p.latest_share) + " focused last time, against " + pct(p.first_share) + " in their first 3 sessions: about " + (p.minutes_more >= 0 ? "+" : "") + p.minutes_more + " min. A before-and-after, not an experiment.</span>"
        : '<span class="caption eyebrow">Time won back · ' + esc(p.participant) + '</span><span class="body-sm muted">After 4 sessions with my eyes on, I\'ll compare the latest with their first three (' + p.sessions + " so far).</span>";
    });
  }

  function renderStory(name) {
    analyze.open = -1;
    main.innerHTML = tabs("story", name) + '<section class="analyze" id="analyze"><p class="body muted">Reading the session…</p></section>';
    getJSON("/api/v5/session?name=" + encodeURIComponent(name)).then(function (s) {
      if (view !== "story") return;
      if (!s || !s.name) { $("#analyze").innerHTML = '<div class="empty"><h2 class="display-sm">I can\'t find that session.</h2><a class="ds-btn ds-btn--primary" href="#/sessions"><span class="ds-btn__label">See every session</span></a></div>'; return; }
      analyze.story = s;
      drawStory();
    }).catch(function () { $("#analyze").innerHTML = '<p class="body bad-text">The launcher did not answer.</p>'; });
  }

  function chart(s) {
    var W = 1000, H = 200, total = Math.max(1, s.duration_s);
    var x = function (t) { return (Math.max(0, Math.min(total, t)) / total * W).toFixed(1); };
    var y = function (p) { return (H - p * H).toFixed(1); };
    var line = s.curve.map(function (c, i) { return (i ? "L" : "M") + x(c[0]) + " " + y(c[1]); }).join(" ");
    var area = s.curve.length ? line + " L" + x(s.curve[s.curve.length - 1][0]) + " " + H + " L" + x(s.curve[0][0]) + " " + H + " Z" : "";
    var grid = [0.25, 0.5, 0.75].map(function (g) { return '<line x1="0" x2="' + W + '" y1="' + y(g) + '" y2="' + y(g) + '" class="grid"></line>'; }).join("");
    var marks = s.moments.map(function (m, i) {
      return '<line class="mark' + (i === analyze.open ? " is-on" : "") + '" x1="' + x(m.t) + '" x2="' + x(m.t) + '" y1="0" y2="' + H + '"></line>';
    }).join("");
    // The numbered pins are HTML on top of the plot, so they stay round and can be focused.
    var pins = s.moments.map(function (m, i) {
      return '<button type="button" class="pin' + (i === analyze.open ? " is-on" : "") + '" data-act="moment" data-index="' + i + '" style="left: ' + (x(m.t) / W * 100).toFixed(2) + '%;"'
        + ' aria-label="' + esc("Moment " + (i + 1) + ": " + behaviorName(m.behavior) + ", " + m.clock) + '">' + (i + 1) + "</button>";
    }).join("");
    var ticks = [];
    var step = total > 5400 ? 1800 : total > 1800 ? 600 : total > 600 ? 300 : 60;
    for (var t = 0; t <= total; t += step) ticks.push('<span style="left: ' + (t / total * 100).toFixed(2) + '%;">' + Math.round(t / 60) + " min</span>");
    return '<div class="chart" id="chart"><div class="chart__y caption muted"><span>100%</span><span>50%</span><span>0%</span></div>'
      + '<div class="chart__plot"><svg viewBox="0 0 ' + W + " " + H + '" preserveAspectRatio="none" role="img" aria-label="How likely they were focused, over the session">'
      + grid + '<path d="' + area + '" class="area"></path><path d="' + line + '" class="line" vector-effect="non-scaling-stroke"></path>' + marks
      + '<line class="cross" id="cross" x1="0" x2="0" y1="0" y2="' + H + '" hidden></line></svg>'
      + pins + '<div class="tip" id="tip" hidden></div></div>'
      + '<div class="chart__x caption muted">' + ticks.join("") + "</div></div>";
  }

  function drawStory() {
    var s = analyze.story, box = $("#analyze");
    if (!s || !box) return;
    var n = s.moments.length;
    var good = s.moments.filter(function (m) { return m.outcome === "success"; }).length;
    var sentence = (s.has_state && s.focused_share != null ? s.participant + " was focused " + pct(s.focused_share) + " of the time at the desk. " : "")
      + (n ? "I stepped in " + n + (n === 1 ? " time" : " times") + (s.by_hand ? " (" + s.by_hand + " by you)" : "") + ", and " + good + " worked." : "Nobody stepped in this time.");
    box.innerHTML = '<div class="storyhead"><div style="flex: 1; min-width: 260px;"><span class="caption muted">' + esc(dayOf(s) + " · " + s.clock + "–" + s.end_clock + " · " + minutes(s.duration_s) + " · " + labelOf(MODES, s.mode)) + "</span>"
      + '<h2 class="display-sm">' + esc(s.participant) + '</h2><p class="body-lg" style="margin: 0;">' + esc(sentence) + "</p></div>"
      + '<a class="ds-btn ds-btn--secondary" href="#/sessions"><span class="ds-btn__label">All sessions</span></a></div>'
      + '<div class="storygrid"><div class="storymain">'
      + '<section class="card"><div class="cardhead"><span class="headline">Their focus, minute by minute</span><span class="caption muted">How likely I thought they were focused. The numbers are the moments I stepped in: click one.</span></div>'
      + (s.curve.length ? chart(s) + strip(s.strip, s.duration_s, false) + legend(s.strip) : '<p class="body-sm muted">My eyes weren\'t on in this session, so there is no focus curve.</p>')
      + "</section>"
      + '<section class="card"><div class="cardhead"><span class="headline">The moments</span><span class="caption muted">Click one to see why.</span></div>'
      + (n ? '<ol class="moments">' + s.moments.map(function (m, i) {
        var tone = m.outcome === "success" ? "success" : m.outcome ? "" : "accent";
        return '<li><button type="button" class="moment' + (i === analyze.open ? " is-on" : "") + '" data-act="moment" data-index="' + i + '"><span class="moment__n">' + (i + 1) + "</span>"
          + '<span class="moment__text"><b class="body-sm">' + esc(behaviorName(m.behavior)) + '</b><span class="caption muted">' + esc(m.clock) + " · " + (m.by === "you" ? "by you" : "by me") + "</span></span>"
          + '<span class="ds-badge' + (tone ? " ds-badge--" + tone : "") + '">' + esc(OUTCOME[m.outcome] || "No verdict") + "</span></button></li>";
      }).join("") + "</ol>" : '<p class="body-sm muted">No nudges in this session.</p>')
      + (s.marks.length || s.notes.length ? '<div class="nowsplit"><span class="caption eyebrow">What you marked</span><ul class="loglist">'
        + s.marks.map(function (k) { return "<li><span class=\"caption muted\">" + esc(k.clock) + '</span><span class="body-sm">' + esc(MARKER[k.marker] || cap(k.marker)) + "</span></li>"; }).join("")
        + s.notes.map(function (k) { return "<li><span class=\"caption muted\">" + esc(k.clock) + '</span><span class="body-sm">Note: ' + esc(k.text) + "</span></li>"; }).join("") + "</ul></div>" : "")
      + "</section></div>"
      + '<aside class="card why-panel" id="why"' + (analyze.open < 0 ? " hidden" : "") + ">" + (analyze.open >= 0 ? explain(s.moments[analyze.open], analyze.open) : "") + "</aside></div>";
  }

  function explain(m, i) {
    if (!m) return "";
    var probs = m.probs || {};
    var shown = STATE_ORDER.filter(function (k) { return probs[k] != null; });
    var ev = (m.evidence || []).map(function (e) { return typeof e === "string" ? e : (e.feature || e.name || "") + (e.value != null ? " " + e.value : ""); }).filter(Boolean);
    return '<div class="ds-modal__head"><h2 class="ds-modal__title">' + (i + 1) + " · " + esc(behaviorName(m.behavior)) + "</h2>"
      + '<button type="button" class="ds-iconbtn ds-modal__close" data-act="moment-close" aria-label="Close">' + icon("X", 18) + "</button></div>"
      + '<span class="caption muted">' + esc(m.clock) + " · " + (m.by === "you" ? "you played it" : "I chose it") + (m.tone ? " · " + (m.tone === "direct" ? "direct" : "gentle") + " tone" : "") + "</span>"
      + (m.sentence || m.reason ? '<div class="nowsplit"><span class="caption eyebrow">Why</span><p class="body" style="margin: 0;">' + esc(m.sentence || cap(m.reason)) + "</p></div>" : "")
      + (m.spoken ? '<div class="nowsplit"><span class="caption eyebrow">What I said</span><p class="body-sm" style="margin: 0;">"' + esc(m.spoken) + '"</p></div>' : "")
      + (shown.length ? '<div class="nowsplit"><span class="caption eyebrow">What I saw' + (m.confidence != null ? " · " + pct(m.confidence) + " sure" : "") + '</span><div class="bars">' + shown.map(function (k) {
        return '<div class="bar"><span class="caption">' + esc(STATE_NAME[k] || cap(k)) + '</span><span class="bar__track"><span class="bar__fill sbg--' + k + '" style="width: ' + (Math.max(0, Math.min(1, probs[k])) * 100).toFixed(0) + '%;"></span></span><span class="caption muted">' + pct(probs[k]) + "</span></div>";
      }).join("") + "</div></div>" : "")
      + (ev.length ? '<div class="nowsplit"><span class="caption eyebrow">Strongest signs</span><ul class="body-sm signs">' + ev.map(function (e) { return "<li>" + esc(e) + "</li>"; }).join("") + "</ul></div>" : "")
      + (m.contrast ? '<div class="nowsplit"><span class="caption eyebrow">Why not something else</span><p class="body-sm" style="margin: 0;">' + esc(m.contrast) + "</p></div>" : "")
      + '<div class="nowsplit"><span class="caption eyebrow">What came of it</span><p class="body-sm" style="margin: 0;">' + esc(OUTCOME[m.outcome] || "No verdict yet")
      + (m.seconds != null ? " · after " + esc(Math.round(m.seconds)) + " s" : "") + (m.detail ? " · " + esc(m.detail) : "") + "</p></div>"
      + (m.propensity != null ? '<span class="caption muted">I picked this with a ' + pct(m.propensity) + " chance.</span>" : "");
  }

  function chartHover(e) {
    var s = analyze.story, svg = e.target.closest && e.target.closest(".chart__plot");
    var tip = $("#tip"), cross = $("#cross");
    if (!s || !svg || !tip || !s.curve.length) return;
    var box = svg.getBoundingClientRect(), frac = (e.clientX - box.left) / box.width;
    var t = frac * Math.max(1, s.duration_s), best = s.curve[0];
    s.curve.forEach(function (c) { if (Math.abs(c[0] - t) < Math.abs(best[0] - t)) best = c; });
    var px = best[0] / Math.max(1, s.duration_s);
    cross.setAttribute("x1", (px * 1000).toFixed(1)); cross.setAttribute("x2", (px * 1000).toFixed(1)); cross.removeAttribute("hidden");
    tip.hidden = false;
    tip.style.left = Math.min(box.width - 150, Math.max(0, px * box.width + 10)) + "px";
    tip.innerHTML = "<b>" + Math.round(best[0] / 60) + " min</b> · " + pct(best[1]) + " focused";
  }
  function chartLeave() { var tip = $("#tip"), cross = $("#cross"); if (tip) tip.hidden = true; if (cross) cross.setAttribute("hidden", ""); }

  var BUCKET = {
    refocus_early: ["Drifting off, early on", "In their first 45 minutes of work"],
    refocus_late: ["Drifting off, after a long stretch", "After 45 minutes without a break"],
    bedtime_early: ["Getting tired, in the evening", "Before 23:00"],
    bedtime_late: ["Getting tired, late at night", "From 23:00"],
    ambiguous: ["Hard to tell", "Drifting or tired: I wasn't sure which"]
  };

  function renderLearned() {
    main.innerHTML = tabs("learned", analyze.story && analyze.story.name) + '<section class="analyze" id="analyze"><p class="body muted">Looking at what I learned…</p></section>';
    Promise.all([post("/api/bandit/run", { kind: "list" }), getJSON("/api/v5/sessions")]).then(function (r) {
      var codes = [];
      ((r[0] && r[0].participants) || []).forEach(function (p) { if (codes.indexOf(p.participant) < 0) codes.push(p.participant); });
      ((r[1] && r[1].sessions) || []).forEach(function (s) { if (s.participant && codes.indexOf(s.participant) < 0) codes.push(s.participant); });
      codes.sort();
      analyze.codes = codes;
      analyze.listMsg = r[0] && r[0].ok === false ? r[0].message : "";
      var last = lastRun();
      if (!analyze.who || codes.indexOf(analyze.who) < 0) analyze.who = last && codes.indexOf(last.participant) >= 0 ? last.participant : codes[0] || "";
      loadLearned();
    });
  }

  function loadLearned() {
    var box = $("#analyze");
    if (!box) return;
    if (!analyze.who) {
      box.innerHTML = '<div class="empty"><img src="img/eepy-scientist.svg" alt=""><h2 class="display-sm">Nobody to learn about yet.</h2><p class="body muted">' + esc(analyze.listMsg || "I learn in the “I estimate and learn” mode, one participant at a time.") + "</p></div>";
      return;
    }
    analyze.learned = null;
    box.innerHTML = '<div class="analyze__top">' + whoPicker(analyze.codes, analyze.who, false) + '</div><p class="body muted">Working it out…</p>';
    post("/api/bandit/run", { kind: "show", participant: analyze.who }).then(function (res) {
      if (view !== "learned") return;
      analyze.learned = res;
      if (!analyze.bucket || !(res.buckets || {})[analyze.bucket]) analyze.bucket = "refocus_early";
      drawLearned();
    });
  }

  function certainty(n) {
    var k = n >= 6 ? 3 : n >= 3 ? 2 : n >= 1 ? 1 : 0;
    return '<span class="dots" title="' + (n ? Math.round(n) + " tries" : "no tries yet") + '">' + [0, 1, 2].map(function (i) { return '<span class="' + (i < k ? "on" : "") + '"></span>'; }).join("") + "</span>";
  }
  function armName(a) { return a.arm === "wait" ? "Waiting" : cap(a.words || a.arm); }

  function drawLearned() {
    var box = $("#analyze"), L = analyze.learned;
    if (!box || !L) return;
    var top = '<div class="analyze__top">' + whoPicker(analyze.codes, analyze.who, false)
      + '<div class="card won"><span class="caption eyebrow">How to read this</span><span class="body-sm muted">For each situation I try moves and keep score of what helped ' + esc(analyze.who)
      + '. More dots: more tries, so I\'m surer. I only learn in the “I estimate and learn” mode.</span></div></div>';
    if (!L.ok) { box.innerHTML = top + '<p class="body bad-text">' + esc(L.message || "I couldn't read what I learned.") + "</p>"; return; }
    var buckets = L.buckets || {};
    var keysB = Object.keys(BUCKET).filter(function (k) { return buckets[k]; });
    var cards = keysB.map(function (k) {
      var arms = buckets[k].slice().sort(function (a, b) { return b.mean - a.mean; });
      var best = arms[0] || {};
      var tries = arms.reduce(function (sum, a) { return sum + (a.n || 0); }, 0);
      return '<button type="button" class="card situation' + (k === analyze.bucket ? " is-on" : "") + '" data-act="bucket" data-value="' + k + '">'
        + '<span class="caption eyebrow">' + esc(BUCKET[k][0]) + '</span><span class="caption muted">' + esc(BUCKET[k][1]) + "</span>"
        + '<span class="headline">' + esc(armName(best)) + "</span>"
        + '<span class="caption muted">' + (tries ? "my best bet so far · " + Math.round(tries) + " tries in all" : "my starting guess · no tries yet") + "</span>" + certainty(best.n || 0) + "</button>";
    }).join("");
    var arms = (buckets[analyze.bucket] || []).slice().sort(function (a, b) { return b.mean - a.mean; });
    var table = '<table class="compare"><thead><tr><th class="caption">Move</th><th class="caption">Helps or not</th><th class="caption">How sure</th><th class="caption">Tries</th></tr></thead><tbody>'
      + arms.map(function (a) {
        var m = Math.max(-1, Math.min(1, a.mean));
        var bar = '<span class="div"><span class="div__mid"></span><span class="div__bar ' + (m >= 0 ? "pos" : "neg") + '" style="' + (m >= 0 ? "left: 50%;" : "right: 50%;") + " width: " + (Math.abs(m) * 50).toFixed(1) + '%;"></span></span>';
        return "<tr><td class=\"body-sm\">" + esc(armName(a)) + '</td><td><span class="divcell">' + bar + '<span class="caption muted">' + (m >= 0 ? "+" : "") + m.toFixed(2) + "</span></span></td><td>" + certainty(a.n || 0) + (a.sd ? ' <span class="caption muted">±' + Number(a.sd).toFixed(2) + "</span>" : "") + '</td><td class="caption">' + Math.round(a.n || 0) + "</td></tr>";
      }).join("") + "</tbody></table>";
    box.innerHTML = top
      + (L.updates ? "" : '<p class="body-sm muted">I haven\'t learned anything about ' + esc(analyze.who) + " yet: these are my starting guesses.</p>")
      + '<div class="situations">' + cards + "</div>"
      + '<section class="card"><div class="cardhead"><span class="headline">Every move, for “' + esc((BUCKET[analyze.bucket] || [analyze.bucket])[0]) + '”</span><span class="caption muted">Right of the line: it helped. Left: it didn\'t.</span></div>' + table + "</section>"
      + '<div class="actions"><button type="button" class="ds-btn ds-btn--primary" data-act="forget-open"' + (L.updates ? "" : " disabled") + '><span class="ds-btn__label">Forget what I learned about ' + esc(analyze.who) + "</span></button>"
      + '<span class="caption muted" id="learned-msg">' + esc(analyze.msg) + "</span></div>";
  }

  function openForget() {
    openModal('<div class="ds-modal" role="dialog" aria-modal="true" aria-labelledby="m-title">'
      + modalHead("Forget what I learned about " + esc(analyze.who) + "?", "I go back to my starting guesses for them. Their recorded sessions stay; only what I learned from them is cleared.")
      + '<div class="ds-modal__actions"><button type="button" class="ds-btn ds-btn--secondary" data-act="modal-close"><span class="ds-btn__label">Keep it</span></button>'
      + '<button type="button" class="ds-btn ds-btn--primary" data-act="forget-confirm"><span class="ds-btn__label">Forget</span></button></div></div>');
  }

  // --- wiring ------------------------------------------------------------------------------------

  main.addEventListener("click", function (e) {
    var el = e.target.closest("[data-act]");
    if (!el || el.disabled) return;
    var act = el.getAttribute("data-act");
    if (act === "appearance") setAppearance(el.getAttribute("data-value"));
    else if (act === "same") {
      var last = lastRun();
      if (!last || !status) return;
      el.disabled = true;
      launch(last.participant, { backend: last.backend, mode: last.mode, robot_host: status.settings.robot_host || "" }).catch(function (err) {
        el.disabled = false;
        var msg = $("#home-msg"); if (msg) msg.textContent = err.message;
      });
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
    else if (act === "retry") { startIntent = true; ask = { busy: false, error: "", other: "" }; post("/api/start-all", {}).then(refresh); }
    else if (act === "begin") { ask = { busy: false, error: "", other: "" }; beginSession(); if (view === "starting") renderStarting(); }
    else if (act === "end-other") {
      el.disabled = true;
      panelPost("session/end", {}).then(function (res) {
        if (!res.ok) { ask.error = res.message || "That session didn't end."; renderStarting(); return; }
        ask = { busy: false, error: "", other: "" };
        beginSession();
      });
    }
    else if (act === "cal") deskCommand(el.getAttribute("data-value"));
    else if (act === "press") press(buttons[el.getAttribute("data-id")], "click");
    else if (act === "stop-eepy") stopEepy("click");
    else if (act === "tab") {
      ctrl.tab = el.getAttribute("data-value");
      document.querySelectorAll(".ctrltabs .ds-tab").forEach(function (t) {
        var on = t.getAttribute("data-value") === ctrl.tab;
        t.classList.toggle("is-selected", on); t.setAttribute("aria-selected", on ? "true" : "false");
      });
      $("#tab-controls").hidden = ctrl.tab !== "controls";
      $("#tab-hmm").hidden = ctrl.tab !== "hmm";
    }
    else if (act === "idle") {
      var on = el.getAttribute("aria-checked") !== "true";
      panelPost("idle", { on: on }).then(function (r) { answer(r, function () { return on ? "I breathe while waiting." : "I stay still while waiting."; }); });
    }
    else if (act === "tone") { ctrl.tone = el.getAttribute("data-value"); updateControl(); }
    else if (act === "autonomy") {
      var pause = el.getAttribute("data-value") === "pause";
      el.disabled = true;
      panelPost("autonomy", { paused: pause }).then(function (r) { answer(r, function () { return pause ? "I'm holding back: I won't step in by myself." : "I can step in again."; }); });
    }
    else if (act === "reconnect") {
      el.disabled = true;
      panelPost("reconnect", {}).then(function (r) { el.disabled = false; answer(r, function () { return "Connected to my body again."; }); });
    }
    else if (act === "log-toggle") { ctrl.logOpen = !ctrl.logOpen; $("#sessionlog").hidden = !ctrl.logOpen; $("#sessionlog").__html = null; updateControl(); }
    else if (act === "note-toggle") { ctrl.noteOpen = !ctrl.noteOpen; $("#note-form").hidden = !ctrl.noteOpen; if (ctrl.noteOpen) $("#note-text").focus(); }
    else if (act === "end-open") openEnd();
    else if (act === "shutdown") {
      el.disabled = true;
      post("/api/stop-all", {}).then(function (res) { var m = $("#done-msg"); if (m) m.textContent = res.message || ""; setTimeout(function () { location.hash = "#/"; }, 900); });
    }
    else if (act === "moment") {
      var i = parseInt(el.getAttribute("data-index"), 10);
      analyze.open = analyze.open === i ? -1 : i;
      drawStory();
      var why = $("#why"); if (why && !why.hidden) why.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
    else if (act === "moment-close") { analyze.open = -1; drawStory(); }
    else if (act === "bucket") { analyze.bucket = el.getAttribute("data-value"); drawLearned(); }
    else if (act === "forget-open") openForget();
    else if (act === "part-start" || act === "part-stop" || act === "part-restart") partAction(act, el.getAttribute("data-part"));
    else if (act === "start-all" || act === "stop-all") {
      el.disabled = true;
      post("/api/" + act, {}).then(function (res) { advMsg = res.message || ""; el.disabled = false; refresh(); setTimeout(function () { advMsg = ""; }, 6000); });
    } else if (act === "copy-commands" || act === "copy-install") {
      var copy = act === "copy-install" ? (el.getAttribute("data-part") === "vision" ? INSTALL.vision : INSTALL.robot) : $("#commands").textContent;
      if (navigator.clipboard) navigator.clipboard.writeText(copy).then(function () { el.querySelector(".ds-btn__label").textContent = "Copied"; });
    } else if (act === "clear-log") { logs = []; renderLog(); }
  });

  main.addEventListener("submit", function (e) {
    if (e.target.id === "settings") { e.preventDefault(); saveSettings(); }
    else if (e.target.id === "say-form") {
      e.preventDefault();
      var say = $("#say-text"), text = say.value.trim();
      if (!text) return;
      panelPost("say", { text: text, tone: ctrl.tone, via: "click" }).then(function (r) {
        if (r.ok) say.value = "";
        answer(r, function (x) { return 'Saying: "' + (x.text || text) + '"'; });
      });
    } else if (e.target.id === "note-form") {
      e.preventDefault();
      var note = $("#note-text"), body = note.value.trim();
      if (!body) return;
      panelPost("note", { text: body }).then(function (r) {
        if (r.ok) { note.value = ""; ctrl.noteOpen = false; $("#note-form").hidden = true; }
        answer(r, function () { return "Note saved, with the time."; });
      });
    }
  });

  // The focus curve's hover: the minute and the focus under the pointer.
  main.addEventListener("mousemove", function (e) {
    if (view === "story" && e.target.closest && e.target.closest(".chart__plot")) chartHover(e);
    else if (view === "story") chartLeave();
  });

  // The modal lives outside <main>: its own clicks.
  document.addEventListener("click", function (e) {
    var el = e.target.closest && e.target.closest("#modal [data-act]");
    if (!el || el.disabled) return;
    var act = el.getAttribute("data-act");
    if (act === "modal-close") closeModal();
    else if (act === "end-confirm") endSession(el);
    else if (act === "forget-confirm") {
      el.disabled = true;
      post("/api/bandit/run", { kind: "reset", participant: analyze.who }).then(function (res) {
        closeModal();
        analyze.msg = res.ok ? "Done: I'm back to my starting guesses for " + analyze.who + "." : (res.message || "That didn't work.");
        loadLearned();
        setTimeout(function () { analyze.msg = ""; }, 8000);
      });
    }
  });

  // The panel's keys, as in the classic panel: only on the control panel, never while typing.
  document.addEventListener("keydown", function (e) {
    if ($("#modal")) { if (e.key === "Escape") closeModal(); return; }
    if (view !== "control" || !pstate || e.ctrlKey || e.metaKey || e.altKey) return;
    var t = e.target, tag = (t && t.tagName) || "";
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || (t && t.isContentEditable)) return;
    var panel = pstate.panel || {};
    var name = e.key === " " ? "space" : e.key === "Escape" ? "escape" : e.key === "Tab" ? "tab" : e.key.toLowerCase();
    if ((panel.stop_keys || ["escape", "space"]).indexOf(name) >= 0) { e.preventDefault(); stopEepy("key"); return; }
    if (name === (panel.tone_key || "tab")) { e.preventDefault(); ctrl.tone = ctrl.tone === "hedged" ? "direct" : "hedged"; updateControl(); flash("Tone: " + (ctrl.tone === "direct" ? "direct" : "gentle")); return; }
    if (name === (panel.idle_key || "a")) {
      e.preventDefault();
      var on = !(ps() && ps().idle_on);
      panelPost("idle", { on: on, via: "key" }).then(function (r) { answer(r, function () { return on ? "I breathe while waiting." : "I stay still while waiting."; }); });
      return;
    }
    if (keys[name] && !e.repeat) { e.preventDefault(); press(keys[name], "key"); }
  });

  main.addEventListener("input", function (e) {
    if (e.target.closest && e.target.closest("#settings")) { advDirty = true; return; }
    if (e.target.id === "intensity") { ctrl.intensity = parseFloat(e.target.value); $("#intensity-v").textContent = Math.round(ctrl.intensity * 100) + "%"; return; }
    if (e.target.id === "vol") { $("#vol-v").textContent = e.target.value + "%"; return; }
    if (!draft) return;
    if (e.target.id === "code") { draft.participant = e.target.value.trim().toUpperCase(); showCodeError(""); renderKnown(); updateSetup(); }
    else if (e.target.id === "host") { draft.robot_host = e.target.value; }
  });
  main.addEventListener("change", function (e) {
    if (e.target.id === "appearance") { setAppearance(e.target.value); return; }
    if (e.target.id === "camera") { advDirty = true; return; }
    if (e.target.id === "log-filter") { logFilter = e.target.value; renderLog(); return; }
    if (e.target.id === "follow") { follow = e.target.checked; renderLog(); return; }
    if (e.target.id === "skip-reason") { desk.reason = e.target.value; return; }
    if (e.target.id === "vol") {
      post("/api/v5/volume", { volume: parseInt(e.target.value, 10) }).then(function (r) {
        if (r.ok) { volumeInfo = r; flash("Volume " + r.volume + "%: you should hear a test sound."); }
        else flash(r.message || "The volume didn't change.", true);
      });
      return;
    }
    if (e.target.id === "who") {
      analyze.who = e.target.value;
      if (view === "sessions") drawSessions(); else if (view === "learned") loadLearned();
      return;
    }
    if (!draft || e.target.type !== "radio") return;
    draft[e.target.name] = e.target.value;
    if (e.target.name === "backend") $("#host-field").hidden = draft.backend !== "real";
    updateSetup();
  });

  var SCREENS = ["setup", "starting", "desk", "control", "sessions", "learned", "advanced"];
  var arg = "";               // the session name of #/done/<name> and #/story/<name>

  function route() {
    var h = location.hash.replace(/^#\/?/, "");
    var m = /^(done|story)\/(.+)$/.exec(h);
    if (m) {
      try { arg = decodeURIComponent(m[2]); } catch (e) { arg = m[2]; }
      return m[1];
    }
    arg = "";
    return SCREENS.indexOf(h) >= 0 ? h : "home";
  }

  function show() {
    var next = route();
    if (next !== "setup") draft = null;
    view = next;
    closeModal();
    renderNav();
    if (view === "home") renderHome();
    else if (view === "setup") renderSetup();
    else if (view === "advanced") renderAdvanced();
    else if (view === "desk") renderDesk();
    else if (view === "control") renderControl();
    else if (view === "done") renderDone(arg);
    else if (view === "sessions") renderSessions();
    else if (view === "story") renderStory(arg);
    else if (view === "learned") renderLearned();
    else renderStarting();
    main.focus({ preventScroll: true });
    window.scrollTo(0, 0);
  }

  function refresh() {
    return getJSON("/api/status").then(function (data) {
      var first = !status;
      status = data;
      if (first && view === "setup") renderSetup();
      else if (first && view === "advanced") renderAdvanced();
      else if (view === "advanced") updateAdvanced();
      else if (view === "home") updateHome();
      else if (view === "setup") updateSetup();
      else if (view === "starting") renderStarting();
      // The session screens follow the control panel; the others only need to know if one is recording.
      var live = view === "desk" || view === "control";
      if (!live && !isUp((parts().panel || {}).state) && !status.other_panel) { pstate = null; updateNav(); return; }
      return pollPanel().then(function () {
        if (view === "desk") updateDesk();
        else if (view === "control") updateControl();
        updateNav();
      });
    }).catch(function () { /* the launcher is restarting: try again next second */ });
  }

  applyTheme();
  window.addEventListener("hashchange", show);
  show();
  refresh();
  setInterval(refresh, 1000);
  setInterval(pollLogs, 1000);
})();
